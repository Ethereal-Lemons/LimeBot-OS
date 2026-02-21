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
  type: 'message' | 'status' | 'qr' | 'error' | 'sent' | 'fileSent' | 'reset_success';
  [key: string]: unknown;
}

export class BridgeServer {
  private wss: WebSocketServer | null = null;
  private wa: WhatsAppClient | null = null;
  private clients: Set<WebSocket> = new Set();
  private lastQR: string | null = null;

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
      await this.wa.sendMessage(cmd.to, cmd.text);
      return { type: 'sent', to: cmd.to };
    }
    
    if (cmd.type === 'sendFile') {
      await this.wa.sendFile(cmd.to, cmd.filePath, cmd.caption);
      return { type: 'fileSent', to: cmd.to, filePath: cmd.filePath };
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
