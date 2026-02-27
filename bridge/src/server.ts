/**
 * WebSocket server for Python-Node.js bridge communication.
 */

import { WebSocketServer, WebSocket } from 'ws';
import { WhatsAppClient, InboundMessage } from './whatsapp.js';

interface SendCommand {
  type: 'send';
  to: string;
  text: string;
}

interface SendFileCommand {
  type: 'sendFile';
  to: string;
  filePath: string;
  caption?: string;
}

interface ResetCommand {
  type: 'reset';
}

type BridgeCommand = SendCommand | SendFileCommand | ResetCommand;

interface BridgeMessage {
  type: 'message' | 'status' | 'qr' | 'error' | 'sent' | 'fileSent' | 'reset_success' | 'queued';
  [key: string]: unknown;
}

export class BridgeServer {
  private wss: WebSocketServer | null = null;
  private wa: WhatsAppClient | null = null;
  private clients: Set<WebSocket> = new Set();
  private lastQR: string | null = null;
  private pending: BridgeCommand[] = [];
  private flushing = false;
  private readonly maxQueue = 200;

  constructor(private port: number, private authDir: string) {}

  async start(): Promise<void> {
    // Create WebSocket server
    this.wss = new WebSocketServer({ port: this.port, host: '0.0.0.0' });
    console.log(`🌉 Bridge server listening on ws://localhost:${this.port}`);

    // Initialize WhatsApp client
    this.wa = new WhatsAppClient({
      authDir: this.authDir,
      onMessage: (msg) => this.broadcast({ type: 'message', ...msg }),
      onQR: (qr) => {
        this.lastQR = qr;
        this.broadcast({ type: 'qr', qr });
      },
      onStatus: (status, selfId) => {
        if (status === 'connected') this.lastQR = null;
        this.broadcast({ type: 'status', status, selfId });
        if (status === 'connected') {
          void this.flushQueue();
        }
      },
    });

    // Handle WebSocket connections
    this.wss.on('connection', (ws) => {
      console.log('🔗 Python client connected');
      this.clients.add(ws);
      
      // Send cached QR if available
      if (this.lastQR) {
        ws.send(JSON.stringify({ type: 'qr', qr: this.lastQR }));
      }

      ws.on('message', async (data) => {
        try {
          const cmd = JSON.parse(data.toString()) as BridgeCommand;
          const result = await this.handleCommand(cmd);
          ws.send(JSON.stringify(result));
        } catch (error) {
          console.error('Error handling command:', error);
          ws.send(JSON.stringify({ type: 'error', error: String(error) }));
        }
      });

      ws.on('close', () => {
        console.log('🔌 Python client disconnected');
        this.clients.delete(ws);
      });

      ws.on('error', (error) => {
        console.error('WebSocket error:', error);
        this.clients.delete(ws);
      });
    });

    // Connect to WhatsApp
    await this.wa.connect();
  }

  private async handleCommand(cmd: BridgeCommand): Promise<BridgeMessage> {
    if (!this.wa) {
      return { type: 'error', error: 'WhatsApp not connected' };
    }
    
    if (cmd.type === 'send') {
      if (!this.wa.isConnected()) {
        this.enqueue(cmd);
        return { type: 'queued', to: cmd.to };
      }
      try {
        await this.wa.sendMessage(cmd.to, cmd.text);
        return { type: 'sent', to: cmd.to };
      } catch (error) {
        this.enqueue(cmd);
        return { type: 'queued', to: cmd.to, error: String(error) };
      }
    }
    
    if (cmd.type === 'sendFile') {
      if (!this.wa.isConnected()) {
        this.enqueue(cmd);
        return { type: 'queued', to: cmd.to, filePath: cmd.filePath };
      }
      try {
        await this.wa.sendFile(cmd.to, cmd.filePath, cmd.caption);
        return { type: 'fileSent', to: cmd.to, filePath: cmd.filePath };
      } catch (error) {
        this.enqueue(cmd);
        return { type: 'queued', to: cmd.to, filePath: cmd.filePath, error: String(error) };
      }
    }

    if (cmd.type === 'reset') {
      console.log('🔄 Resetting WhatsApp session...');
      if (this.wa) {
        await this.wa.disconnect();
      }
      
      // Clean up session directory
      const fs = await import('fs');
      if (fs.existsSync(this.authDir)) {
          fs.rmSync(this.authDir, { recursive: true, force: true });
          console.log('🗑️ Session directory cleared');
      }

      // Reconnect (will generate new QR)
      if (this.wa) {
           await this.wa.connect();
      }
      return { type: 'reset_success' };
    }
    
    return { type: 'error', error: 'Unknown command type' };
  }

  private enqueue(cmd: BridgeCommand): void {
    this.pending.push(cmd);
    if (this.pending.length > this.maxQueue) {
      this.pending.shift();
    }
  }

  private async flushQueue(): Promise<void> {
    if (this.flushing || !this.wa || !this.wa.isConnected()) return;
    this.flushing = true;

    try {
      while (this.pending.length > 0 && this.wa.isConnected()) {
        const cmd = this.pending.shift();
        if (!cmd) break;

        const maxAttempts = 3;
        let attempt = 0;
        let sent = false;

        while (attempt < maxAttempts && !sent && this.wa.isConnected()) {
          attempt += 1;
          try {
            if (cmd.type === 'send') {
              await this.wa.sendMessage(cmd.to, cmd.text);
            } else if (cmd.type === 'sendFile') {
              await this.wa.sendFile(cmd.to, cmd.filePath, cmd.caption);
            }
            sent = true;
          } catch (err) {
            const delayMs = 500 * Math.pow(2, attempt);
            await new Promise((r) => setTimeout(r, delayMs));
            if (attempt >= maxAttempts) {
              console.error('Failed to send queued command:', err);
            }
          }
        }
      }
    } finally {
      this.flushing = false;
    }
  }

  private broadcast(msg: BridgeMessage): void {
    const data = JSON.stringify(msg);
    for (const client of this.clients) {
      if (client.readyState === WebSocket.OPEN) {
        client.send(data);
      }
    }
  }

  async stop(): Promise<void> {
    // Close all client connections
    for (const client of this.clients) {
      client.close();
    }
    this.clients.clear();

    // Close WebSocket server
    if (this.wss) {
      this.wss.close();
      this.wss = null;
    }

    // Disconnect WhatsApp
    if (this.wa) {
      await this.wa.disconnect();
      this.wa = null;
    }
  }
}
