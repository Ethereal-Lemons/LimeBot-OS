/**
 * WhatsApp client wrapper using Baileys.
 * Based on NanoBot's working implementation.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import makeWASocket, {
  DisconnectReason,
  downloadMediaMessage,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
} from '@whiskeysockets/baileys';

import { Boom } from '@hapi/boom';
import qrcode from 'qrcode-terminal';
import pino from 'pino';
import path from 'path';
import { fileURLToPath } from 'url';

const BRIDGE_PROJECT_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
);

const VERSION = '1.0.0';

export interface InboundMessage {
  id: string;
  sender: string;
  senderAlt?: string;
  content: string;
  timestamp: number;
  isGroup: boolean;
  pushName?: string;
  verifiedName?: string;
  audio?: InboundAudio;
}

export interface InboundAudio {
  path: string;
  mimetype: string;
  fileName: string;
  durationSeconds?: number;
}

export interface WhatsAppClientOptions {
  authDir: string;
  onMessage: (msg: InboundMessage) => void | Promise<void>;
  onQR: (qr: string) => void;
  onStatus: (status: string, selfId?: string) => void;
}

export class WhatsAppClient {
  private sock: any = null;
  private options: WhatsAppClientOptions;
  private reconnecting = false;
  private connected = false;
  private connecting = false;
  private reconnectAttempts = 0;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private connectPromise: Promise<void> | null = null;
  private lidToJid: Map<string, string> = new Map();
  private recentMessageIds: Map<string, number> = new Map();

  constructor(options: WhatsAppClientOptions) {
    this.options = options;
  }

  async connect(): Promise<void> {
    if (this.connectPromise) return this.connectPromise;
    if (this.connecting) return;
    this.connecting = true;

    this.connectPromise = this._connectInternal()
      .catch((err) => {
        console.error('Failed to connect WhatsApp client:', err);
        this.scheduleReconnect();
      })
      .finally(() => {
        this.connecting = false;
        this.connectPromise = null;
      });

    return this.connectPromise;
  }

  private async _connectInternal(): Promise<void> {
    const logger = pino({ level: 'silent' });
    const { state, saveCreds } = await useMultiFileAuthState(this.options.authDir);
    const { version } = await fetchLatestBaileysVersion();

    console.log(`Using Baileys version: ${version.join('.')}`);

    // Diagnostic check for "ghost" session state
    if (state.creds && state.creds.me && !state.creds.registered) {
      console.warn('⚠️ WARNING: Session credentials exist but marked as unregistered.');
      console.warn('This usually causes WhatsApp to prompt for a QR code unnecessarily.');
      console.warn('Scan the QR code again to fix, or use the "Reset" command if issues persist.');
    }

    // Create socket following NanoBot's pattern
    this.sock = makeWASocket({
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger),
      },
      version,
      logger,
      printQRInTerminal: false,
      browser: ['LimeBot', 'cli', VERSION],
      syncFullHistory: false,
      markOnlineOnConnect: false,
    });

    // Handle WebSocket errors
    if (this.sock.ws && typeof this.sock.ws.on === 'function') {
      this.sock.ws.on('error', (err: Error) => {
        console.error('WebSocket error:', err.message);
      });
    }

    // Handle connection updates
    this.sock.ev.on('connection.update', async (update: any) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        // Display QR code in terminal
        console.log('\n📱 Scan this QR code with WhatsApp (Linked Devices):\n');
        qrcode.generate(qr, { small: true });
        this.options.onQR(qr);
      }

      if (connection === 'close') {
        const statusCode = (lastDisconnect?.error as Boom)?.output?.statusCode;
        const errorDetail = (lastDisconnect?.error as Boom)?.message || 'No error message';
        const shouldReconnect = statusCode !== DisconnectReason.loggedOut;

        console.log(`Connection closed. Status: ${statusCode}, Error: ${errorDetail}, Will reconnect: ${shouldReconnect}`);

        switch (statusCode) {
          case DisconnectReason.loggedOut:
            console.error('❌ Logged out of WhatsApp. You must scan the QR code again.');
            break;
          case DisconnectReason.restartRequired:
            console.log('🔄 Restart required, reconnecting...');
            break;
          case DisconnectReason.connectionLost:
            console.warn('📡 Connection lost, will attempt to reconnect...');
            break;
          case DisconnectReason.badSession:
            console.error('🛑 Bad session file. If this persists, please reset WhatsApp session.');
            break;
          default:
            console.log(`Disconnect reason: ${statusCode}`);
        }

        this.options.onStatus('disconnected');
        this.connected = false;

        if (shouldReconnect && !this.reconnecting) {
          this.scheduleReconnect();
        }
      } else if (connection === 'open') {
        console.log('✅ Connected to WhatsApp');
        // Extract self ID for self-message filtering
        const selfId = this.sock.user?.id?.split(':')[0] || this.sock.user?.id?.split('@')[0] || '';
        console.log(`Connected as: ${selfId}`);
        this.connected = true;
        this.reconnecting = false;
        this.reconnectAttempts = 0;
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }
        this.options.onStatus('connected', selfId);
      }
    });

    // Save credentials on update
    this.sock.ev.on('creds.update', async () => {
      console.log('[WhatsApp] Updating credentials...');
      await saveCreds();
      if (this.sock.creds?.registered) {
        console.log('✅ Session now marked as REGISTERED');
      }
    });

    // Handle contact updates to map LIDs to JIDs
    this.sock.ev.on('contacts.upsert', (contacts: any[]) => {
      for (const contact of contacts) {
        if (contact.id && contact.lid) {
          this.lidToJid.set(contact.lid, contact.id);
          console.log(`[WhatsApp] Mapped LID ${contact.lid} to JID ${contact.id}`);
        }
      }
    });

    this.sock.ev.on('contacts.update', (updates: any[]) => {
      for (const update of updates) {
        if (update.id && update.lid) {
          this.lidToJid.set(update.lid, update.id);
          console.log(`[WhatsApp] Updated LID mapping: ${update.lid} -> ${update.id}`);
        }
      }
    });

    // Handle incoming messages
    this.sock.ev.on('messages.upsert', async ({ messages, type }: { messages: any[]; type: string }) => {
      if (type !== 'notify') return;

      for (const msg of messages) {
        const msgId = msg.key?.id;
        if (msgId && this.isDuplicateMessage(msgId)) {
          continue;
        }
        // Do not feed LimeBot's own outgoing replies back into the agent. Baileys
        // emits self-sent messages through the same notify stream as inbound
        // messages; forwarding them creates recursive turns and duplicate replies.
        if (msg.key.fromMe) continue;

        // Skip status updates
        if (msg.key.remoteJid === 'status@broadcast') continue;

        let sender = msg.key.remoteJid || '';
        let senderAlt = (msg.key as any).remoteJidAlt || (msg.key as any).participantAlt || undefined;

        // Resolve LID to JID if possible
        if (sender.endsWith('@lid')) {
          const resolved = this.lidToJid.get(sender);
          if (resolved) {
            console.log(`[WhatsApp] Resolved LID ${sender} to JID ${resolved}`);
            sender = resolved;
          } else {
            console.log(`[WhatsApp] Could not resolve LID ${sender} yet.`);
          }
        }

        const message = msg.message;
        const audio = message?.audioMessage
          ? await this.downloadInboundAudio(msg)
          : undefined;
        const content = this.extractMessageContent(msg);
        if (!content && !audio) continue;

        const isGroup = msg.key.remoteJid?.endsWith('@g.us') || false;

        await this.options.onMessage({
          id: msg.key.id || '',
          sender,
          senderAlt,
          content: content || '[Voice Message]',
          timestamp: msg.messageTimestamp as number,
          isGroup,
          pushName: msg.pushName || undefined,
          verifiedName: msg.verifiedName || undefined,
          audio,
        });
      }
    });
  }

  isConnected(): boolean {
    return this.connected;
  }

  private isDuplicateMessage(id: string): boolean {
    const now = Date.now();
    const existing = this.recentMessageIds.get(id);
    if (existing) return true;

    this.recentMessageIds.set(id, now);
    // Basic TTL + size cap to avoid unbounded growth.
    if (this.recentMessageIds.size > 1000) {
      const cutoff = now - 5 * 60 * 1000;
      for (const [key, ts] of this.recentMessageIds.entries()) {
        if (ts < cutoff) {
          this.recentMessageIds.delete(key);
        }
      }
    }
    return false;
  }

  private scheduleReconnect(): void {
    if (this.reconnecting) return;
    this.reconnecting = true;

    const baseDelayMs = 2000;
    const maxDelayMs = 60000;
    const attempt = Math.min(this.reconnectAttempts, 6);
    const delay = Math.min(maxDelayMs, baseDelayMs * Math.pow(2, attempt));
    const jitter = Math.floor(delay * (0.2 * Math.random()));
    const totalDelay = delay + jitter;

    this.reconnectAttempts += 1;

    console.log(`Reconnecting in ${Math.round(totalDelay / 1000)} seconds...`);
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.reconnecting = false;
      this.connect();
    }, totalDelay);
  }

  private extractMessageContent(msg: any): string | null {
    const message = msg.message;
    if (!message) return null;

    // Text message
    if (message.conversation) {
      return message.conversation;
    }

    // Extended text (reply, link preview)
    if (message.extendedTextMessage?.text) {
      return message.extendedTextMessage.text;
    }

    // Image with caption
    if (message.imageMessage?.caption) {
      return `[Image] ${message.imageMessage.caption}`;
    }

    // Video with caption
    if (message.videoMessage?.caption) {
      return `[Video] ${message.videoMessage.caption}`;
    }

    // Document with caption
    if (message.documentMessage?.caption) {
      return `[Document] ${message.documentMessage.caption}`;
    }

    // Voice/Audio message
    if (message.audioMessage) {
      return `[Voice Message]`;
    }

    return null;
  }

  private async downloadInboundAudio(msg: any): Promise<InboundAudio | undefined> {
    const audioMessage = msg.message?.audioMessage;
    if (!audioMessage) return undefined;

    const fileLength = Number(audioMessage.fileLength || 0);
    const maxBytes = 15 * 1024 * 1024;
    if (fileLength > maxBytes) {
      console.warn(`[WhatsApp] Ignoring oversized audio message (${fileLength} bytes).`);
      return undefined;
    }

    try {
      const fs = await import('fs/promises');
      const path = await import('path');
      const crypto = await import('crypto');
      const buffer = await downloadMediaMessage(msg, 'buffer', {});

      if (!Buffer.isBuffer(buffer) || buffer.length === 0 || buffer.length > maxBytes) {
        console.warn('[WhatsApp] Ignoring empty or oversized downloaded audio message.');
        return undefined;
      }

      const directory = path.resolve(BRIDGE_PROJECT_ROOT, 'temp', 'whatsapp-inbound');
      await fs.mkdir(directory, { recursive: true });
      const mimetype = String(audioMessage.mimetype || 'audio/ogg');
      const extension = mimetype.includes('mpeg') || mimetype.includes('mp3') ? 'mp3' : 'ogg';
      const fileName = `whatsapp_${crypto.randomUUID()}.${extension}`;
      const filePath = path.join(directory, fileName);
      await fs.writeFile(filePath, buffer, { mode: 0o600 });

      return {
        path: filePath,
        mimetype,
        fileName,
        durationSeconds: Number(audioMessage.seconds || 0) || undefined,
      };
    } catch (error) {
      console.error('[WhatsApp] Failed to download inbound audio:', error);
      return undefined;
    }
  }

  private formatJid(jid: string): string {
    if (!jid) return '';
    if (jid.includes('@')) return jid;
    // If it's just digits, assume personal WhatsApp JID
    if (/^\d+$/.test(jid)) {
      return `${jid}@s.whatsapp.net`;
    }
    return jid;
  }

  async sendMessage(to: string, text: string): Promise<void> {
    if (!this.sock) {
      throw new Error('Not connected');
    }

    if (!to) {
      throw new Error('Recipient JID is missing');
    }

    const formattedTo = this.formatJid(to);
    console.log(`Debug: Sending message to ${formattedTo}`);
    await this.sock.sendMessage(formattedTo, { text });
  }

  async sendFile(to: string, filePath: string, caption?: string): Promise<void> {
    if (!this.sock) {
      throw new Error('Not connected');
    }

    const fs = await import('fs');
    const path = await import('path');

    // Read file
    const buffer = fs.readFileSync(filePath);
    console.log(`Debug: Reading file ${filePath}, size: ${buffer.length} bytes`);

    const fileName = path.basename(filePath);
    const ext = path.extname(filePath).toLowerCase();

    // Determine mimetype and message type
    const mimeTypes: Record<string, string> = {
      '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif',
      '.mp4': 'video/mp4', '.mov': 'video/quicktime', '.avi': 'video/x-msvideo',
      '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg', '.wav': 'audio/wav',
      '.pdf': 'application/pdf', '.doc': 'application/msword',
      '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      '.xls': 'application/vnd.ms-excel',
      '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      '.txt': 'text/plain', '.csv': 'text/csv', '.json': 'application/json',
      '.zip': 'application/zip', '.rar': 'application/x-rar-compressed',
    };

    const mimetype = mimeTypes[ext] || 'application/octet-stream';

    const formattedTo = this.formatJid(to);
    if (!formattedTo) {
      throw new Error('Recipient JID is missing or invalid');
    }

    let result;
    // Choose message type based on file type
    if (['.jpg', '.jpeg', '.png', '.gif'].includes(ext)) {
      result = await this.sock.sendMessage(formattedTo, { image: buffer, caption });
    } else if (['.mp4', '.mov', '.avi'].includes(ext)) {
      result = await this.sock.sendMessage(formattedTo, { video: buffer, caption });
    } else if (['.mp3', '.ogg', '.wav'].includes(ext)) {
      result = await this.sock.sendMessage(formattedTo, { audio: buffer, mimetype });
    } else {
      // Send as document
      result = await this.sock.sendMessage(formattedTo, { document: buffer, mimetype, fileName, caption });
    }

    console.log(`📎 File sent: ${fileName} to ${formattedTo}. Msg ID: ${result?.key?.id}`);
  }

  async disconnect(): Promise<void> {
    if (this.sock) {
      this.sock.end(undefined);
      this.sock = null;
    }
    this.connected = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.reconnectAttempts = 0;
    this.reconnecting = false;
  }
}
