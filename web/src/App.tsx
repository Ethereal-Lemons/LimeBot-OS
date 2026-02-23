import { useEffect, useState, useRef } from 'react';
import axios from 'axios';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

import { API_BASE_URL, WS_BASE_URL } from "@/lib/api";
import { AppLayout } from "@/components/layout/AppLayout";
import { ChatInterface } from "@/components/chat/ChatInterface";
import { InstancesList } from "@/components/sessions/SessionsList";
import { ConfigPage } from "@/components/config/ConfigPage";
import { ChannelsPage } from "@/components/channels/ChannelsPage";
import { OverviewPage } from "@/components/overview/OverviewPage";
import { LogsPage } from "@/components/logs/LogsPage";
import { SkillsPage } from "@/components/skills/SkillsPage";
import { AppearancePage } from "@/components/config/AppearancePage";
import { SetupPage } from "@/components/setup/SetupPage";
import { PersonaPage } from "@/components/persona/PersonaPage";
import { AuthKeyModal } from "@/components/auth/AuthKeyModal";
import { CronPage } from "@/components/cron/CronPage";
import { MemoryPage } from "@/components/memory/MemoryPage";
import { injectCss, CSS_STORAGE_KEY } from "@/lib/css-injector";

import { ToolExecution } from "@/components/chat/ToolCard";
import { ConfirmationRequest } from "@/components/chat/ConfirmationCard";
import { GhostActivity } from "@/components/chat/GhostActivity";

type Message = {
  sender: 'user' | 'bot';
  type?: 'text' | 'tool' | 'confirmation';
  content: string;
  thinking?: string;
  image?: string | null;
  toolExecution?: ToolExecution;
  confirmation?: ConfirmationRequest;
  variant?: 'default' | 'destructive' | 'warning';
};

function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isConnected, setIsConnected] = useState(false);
  const [botIdentity, setBotIdentity] = useState<{ name: string, avatar: string | null }>({ name: 'LimeBot', avatar: null });
  const [currentView, setCurrentView] = useState('chat');
  const [forceSetup, setForceSetup] = useState(window.location.pathname === '/setup');
  const [sessionId, setSessionId] = useState(() => crypto.randomUUID());
  const [showAuthModal, setShowAuthModal] = useState(false);
  const ws = useRef<WebSocket | null>(null);

  const [isTyping, setIsTyping] = useState(false);

  // Rate Limit Handling
  const [rateLimitAlertOpen, setRateLimitAlertOpen] = useState(false);

  // Ghost Activity (Memory updates, etc.)
  const [activity, setActivity] = useState<{ text: string } | null>(null);

  // Autonomous Mode Status
  const [autonomousMode, setAutonomousMode] = useState(false);

  // Initialization State (to prevent flash)
  const [isInitialized, setIsInitialized] = useState(false);


  useEffect(() => {
    document.documentElement.classList.add('dark');

    // Load saved theme
    // Load saved theme
    const savedTheme = localStorage.getItem('limebot-theme') || 'lime';
    if (savedTheme.startsWith('custom-')) {
      document.documentElement.removeAttribute('data-theme');
      applyCustomTheme(savedTheme);
    } else if (savedTheme !== 'lime') {
      document.documentElement.setAttribute('data-theme', savedTheme);
    } else {
      document.documentElement.removeAttribute('data-theme');
    }

    const apiKey = localStorage.getItem('limebot_api_key');
    if (apiKey) {
      axios.defaults.headers.common['X-API-Key'] = apiKey;
    }

    const interceptor = axios.interceptors.response.use(
      response => response,
      error => {
        if (error.response?.status === 401 || error.response?.status === 403) {
          setShowAuthModal(true);
        }
        return Promise.reject(error);
      }
    );

    // Apply custom global CSS
    const customCss = localStorage.getItem(CSS_STORAGE_KEY) || '';
    injectCss(customCss);

    const init = async () => {

      const MAX_RETRIES = 5;
      const RETRY_DELAY = 2000;

      for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
        try {
          // 1. Initial configuration check (no auth needed)
          const statusRes = await axios.get(`${API_BASE_URL}/api/setup/status`);
          if (statusRes.data.configured) {
            setForceSetup(false);
          } else {
            setForceSetup(true);
            setIsInitialized(true);
            return;
          }

          // 2. Load authenticated data if configured
          await Promise.all([
            axios.get(`${API_BASE_URL}/api/identity`)
              .then(res => { setBotIdentity(res.data); lastExplicitIdentityFetch.current = Date.now(); }),
            axios.get(`${API_BASE_URL}/api/config`)
              .then(res => {
                if (res.data.env?.AUTONOMOUS_MODE === 'true') {
                  setAutonomousMode(true);
                }
              })
          ]);

          break;
        } catch (err: any) {
          const status = err?.response?.status;

          // If unauthorized, wait for AuthKeyModal
          if (status === 401 || status === 403) break;

          if (!err?.response && attempt < MAX_RETRIES) {
            console.log(`Backend not reachable, retrying in ${RETRY_DELAY}ms (${attempt + 1}/${MAX_RETRIES})...`);
            await new Promise(r => setTimeout(r, RETRY_DELAY));
            continue;
          }

          // All retries exhausted or got a real error response
          console.error("Initialization failed:", err);
          if (!err?.response) setForceSetup(true);
        }
      }
      setIsInitialized(true);
    };

    init();

    connectWebSocket();



    return () => {
      ws.current?.close();
      axios.interceptors.response.eject(interceptor);
    };
  }, []);

  // Helper to apply custom theme variables
  const applyCustomTheme = (themeId: string) => {
    try {
      const savedThemes = localStorage.getItem('limebot-custom-themes');
      if (savedThemes) {
        const themes = JSON.parse(savedThemes);
        const theme = themes.find((t: any) => t.id === themeId);
        if (theme) {
          // Apply variables
          Object.entries(theme.variables).forEach(([key, value]) => {
            document.documentElement.style.setProperty(key, value as string);
          });
          // Apply background image if it exists
          if (theme.bgImage) {
            document.documentElement.style.setProperty('--bg-image', theme.bgImage);
          }
          document.documentElement.setAttribute('data-custom-theme', 'true');
        }
      }
    } catch (e) {
      console.error("Failed to apply custom theme", e);
    }
  };

  const clearCustomThemeVars = () => {
    // We can't easily accept *all* custom vars without a list, but we can look at what we typically set.
    // Or simpler: remove the style attribute for the specific properties we know we set.
    // A better approach for cleanup:
    const propsToRemove = [
      '--primary', '--primary-foreground', '--background', '--foreground',
      '--card', '--card-foreground', '--popover', '--popover-foreground',
      '--border', '--input', '--accent', '--accent-foreground',
      '--ring', '--radius', '--muted', '--muted-foreground', '--bg-image'
    ];
    propsToRemove.forEach(prop => document.documentElement.style.removeProperty(prop));
    document.documentElement.removeAttribute('data-custom-theme');
  };

  const handleThemeChange = (theme: string) => {
    clearCustomThemeVars();

    if (theme === 'lime') {
      document.documentElement.removeAttribute('data-theme');
    } else if (theme.startsWith('custom-')) {
      document.documentElement.removeAttribute('data-theme');
      applyCustomTheme(theme);
    } else {
      document.documentElement.setAttribute('data-theme', theme);
    }
    localStorage.setItem('limebot-theme', theme);
  };

  const handleAuthSuccess = (key: string) => {
    localStorage.setItem('limebot_api_key', key);
    axios.defaults.headers.common['X-API-Key'] = key;
    setShowAuthModal(false);
    axios.defaults.headers.common['X-API-Key'] = key;
    setShowAuthModal(false);
    window.location.reload();
  };



  const connectWebSocket = () => {
    if (ws.current?.readyState === WebSocket.OPEN) return;

    const apiKey = localStorage.getItem('limebot_api_key');
    ws.current = new WebSocket(`${WS_BASE_URL}/ws?api_key=${apiKey || ''}`);

    ws.current.onopen = () => {
      console.log('Connected to LimeBot');
      setIsConnected(true);
    };

    ws.current.onclose = () => {
      console.log('Disconnected from LimeBot');
      setIsConnected(false);

      setIsConnected(false);

      setTimeout(() => {
        console.log('Attempting to reconnect...');
        connectWebSocket();
      }, 3000);
    };

    ws.current.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'message' || data.type === 'full_content') {


          setIsTyping(false);
          let variant: 'default' | 'destructive' | 'warning' = 'default';
          if (data.metadata?.is_error) variant = 'destructive';
          if (data.metadata?.is_warning) variant = 'warning';

          setMessages(prev => {
            // Prevent double-adding identical consecutive messages
            const lastMsg = prev[prev.length - 1];
            if (lastMsg && lastMsg.sender === 'bot' && lastMsg.content === data.content && !data.metadata?.type) { // Fix: Allow updates if it's a stream chunk or similar
              // Actually for full_content we might want to replace.
              // But the existing logic seems to just append.
              // Let's keep it simple for now.
              return prev;
            }
            // Logic for full content replacement or append?
            // Since "full_content" usually comes at the end or as a whole message.
            // If we were streaming, we'd update the last message.

            // Check if we are updating a streaming message
            if (lastMsg && lastMsg.sender === 'bot' && lastMsg.type !== 'tool') {
              // If it was a stream update, we just replace the content
              // But full_content usually means "final" content.
              return prev.map((m, i) => i === prev.length - 1 ? { ...m, content: data.content } : m);
            }

            return [...prev, {
              sender: 'bot',
              content: data.content,
              variant
            }];
          });

          if (data.metadata && data.metadata.identity_updated) {
            // FIX 6: use refreshIdentity so the poll skips for 8 s after this
            refreshIdentity();
          }
        }
        else if (data.type === 'cancellation' || data.metadata?.is_cancellation) {
          setIsTyping(false);
          // Mark all running tools as cancelled/error
          setMessages(prev => prev.map(m => {
            if (m.type === 'tool' && m.toolExecution?.status === 'running') {
              return {
                ...m,
                toolExecution: {
                  ...m.toolExecution,
                  status: 'error',
                  result: 'Cancelled by user.'
                }
              };
            }
            return m;
          }));
        }
        else if (data.type === 'stop_typing' || data.metadata?.type === 'stop_typing') {
          setIsTyping(false);
        }
        else if (data.type === 'typing' || data.metadata?.type === 'typing') {
          setIsTyping(true);
        }
        else if (data.metadata?.type === 'chunk') {
          // Handle streaming text chunk
          setMessages(prev => {
            const lastMsg = prev[prev.length - 1];
            if (lastMsg && lastMsg.sender === 'bot' && lastMsg.type !== 'tool' && !lastMsg.confirmation) {
              const newContent = lastMsg.content + data.content;
              return prev.map((m, i) => i === prev.length - 1 ? { ...m, content: newContent } : m);
            } else {
              // Start new message
              return [...prev, { sender: 'bot', content: data.content }];
            }
          });
        }
        else if (data.metadata?.type === 'thinking') {
          // Handle thinking stream chunk
          setMessages(prev => {
            const lastMsg = prev[prev.length - 1];
            if (lastMsg && lastMsg.sender === 'bot' && lastMsg.type !== 'tool' && !lastMsg.confirmation) {
              const newThinking = (lastMsg.thinking || "") + data.content;
              return prev.map((m, i) => i === prev.length - 1 ? { ...m, thinking: newThinking } : m);
            } else {
              // Start new message with thinking
              return [...prev, { sender: 'bot', content: "", thinking: data.content }];
            }
          });
        }
        else if (data.type === 'rate_limit_error') {
          console.error("Rate Limit Error:", data.metadata?.details);
          setRateLimitAlertOpen(true);
        } else if (data.type === 'tool_execution') {
          const toolData = data.metadata;

          setMessages(prev => {
            const existingIndex = prev.findIndex(m =>
              m.type === 'tool' && m.toolExecution?.tool_call_id === toolData.tool_call_id
            );

            if (existingIndex !== -1) {
              // Update existing tool card
              const newMessages = [...prev];
              const existingExec = newMessages[existingIndex].toolExecution!;

              // If it's a progress update, append to logs
              let updatedLogs = existingExec.logs || [];
              if (toolData.status === 'progress') {
                updatedLogs = [...updatedLogs, data.content];
              }

              newMessages[existingIndex] = {
                ...newMessages[existingIndex],
                toolExecution: {
                  ...existingExec,
                  status: toolData.status === 'progress' ? existingExec.status : toolData.status,
                  result: toolData.result,
                  conf_id: toolData.conf_id || existingExec.conf_id,
                  logs: updatedLogs
                }
              };
              return newMessages;
            } else {
              // Create new tool card
              return [...prev, {
                sender: 'bot',
                type: 'tool',
                content: '',
                toolExecution: {
                  tool: toolData.tool,
                  status: toolData.status,
                  args: toolData.args,
                  tool_call_id: toolData.tool_call_id,
                  conf_id: toolData.conf_id,
                  logs: []
                }
              }];
            }
          });
        } else if (data.type === 'confirmation_request') {
          // Handle confirmation request from backend
          const confData = data.metadata;
          setMessages(prev => [...prev, {
            sender: 'bot',
            type: 'confirmation',
            content: '',
            confirmation: {
              id: confData.id,
              action: confData.action,
              description: confData.description,
              details: confData.details,
              status: 'pending'
            }
          }]);
        } else if (data.metadata?.type === 'activity') {
          // Handle background activity (Ghost Mode)
          console.log("👻 Activity:", data.metadata.text);
          setActivity({ text: data.metadata.text });

          // Auto-clear after 4s (matching component animation)
          setTimeout(() => setActivity(null), 4000);
        }
      } catch (error) {
        console.error('Error parsing message:', error);
      }

    };
  };

  const handleSendMessage = (contentOverride?: string | null, image?: string | null) => {
    const finalContent = contentOverride || inputValue.trim();

    if ((!finalContent && !image) || !ws.current || ws.current.readyState !== WebSocket.OPEN) return;

    setIsTyping(true); // Start typing on send

    // Send object with optional image and session ID
    ws.current.send(JSON.stringify({
      content: finalContent,
      image: image,
      chat_id: sessionId
    }));

    setMessages(prev => [...prev, { sender: 'user', content: finalContent, image: image }]);
    setInputValue("");
  };

  const handleNewChat = () => {
    // Generate new Session ID
    const newId = crypto.randomUUID();
    setSessionId(newId);
    setMessages([]); // Clear chat window
    setIsTyping(false);
    console.log("Started new session:", newId);
  };


  // Poll for identity updates periodically.
  // FIX 6: track when the last *explicit* refresh happened so the background
  // poll never races with (and overwrites) a freshly-fetched identity.
  const lastExplicitIdentityFetch = useRef(0);
  const POLL_SKIP_WINDOW_MS = 8000; // skip poll for 8 s after an explicit fetch

  // Expose a helper the rest of the app can call for explicit refreshes.
  const refreshIdentity = () => {
    lastExplicitIdentityFetch.current = Date.now();
    axios.get(`${API_BASE_URL}/api/identity`)
      .then(res => setBotIdentity(res.data))
      .catch(err => {
        if (err.response?.status !== 401)
          console.error("Failed to refresh identity:", err);
      });
  };

  useEffect(() => {
    const interval = setInterval(() => {
      // Skip if an explicit fetch happened very recently
      if (Date.now() - lastExplicitIdentityFetch.current < POLL_SKIP_WINDOW_MS) return;
      axios.get(`${API_BASE_URL}/api/identity`)
        .then(res => {
          const data = res.data;
          setBotIdentity(prev => {
            if (prev.name !== data.name || prev.avatar !== data.avatar) return data;
            return prev;
          });
        })
        .catch(err => {
          if (err.response?.status !== 401)
            console.error("Failed to poll identity:", err);
        });
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  if (!isInitialized) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-[#0a0a0a]">
        <div className="flex flex-col items-center gap-6">
          <img src="/limesimple.png" alt="LimeBot Logo" className="w-16 h-16 animate-pulse" />
          <div className="flex flex-col items-center gap-4">
            <div className="w-12 h-12 border-4 border-primary/20 border-t-primary rounded-full animate-spin" />
            <p className="text-muted-foreground animate-pulse text-sm font-medium tracking-widest uppercase text-center">Initializing System...</p>
          </div>
        </div>
      </div>
    );
  }

  if (!isInitialized) {
    return (
      <div className="min-h-screen bg-background flex flex-col items-center justify-center p-4">
        <div className="fixed inset-0 bg-[radial-gradient(circle_at_50%_50%,rgba(50,205,50,0.05),transparent_70%)]" />
        <div className="relative space-y-8 text-center animate-in fade-in zoom-in duration-700">
          <div className="inline-block relative">
            <div className="absolute inset-0 bg-primary/20 blur-3xl rounded-full scale-150 animate-pulse" />
            <img src="/lime.png" alt="LimeBot" className="h-40 w-auto relative drop-shadow-2xl" />
          </div>
          <div className="space-y-3">
            <h1 className="text-4xl font-black tracking-tighter text-foreground drop-shadow-sm">
              LIME<span className="text-primary italic">BOT</span>
            </h1>
            <div className="flex items-center justify-center gap-2">
              <div className="h-1.5 w-1.5 rounded-full bg-primary animate-bounce" />
              <div className="h-1.5 w-1.5 rounded-full bg-primary animate-bounce [animation-delay:0.2s]" />
              <div className="h-1.5 w-1.5 rounded-full bg-primary animate-bounce [animation-delay:0.4s]" />
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-widest ml-1">
                Connecting to backend
              </span>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (forceSetup) {
    return <SetupPage />;
  }

  return (
    <AppLayout
      botIdentity={botIdentity}
      activeView={currentView}
      onNavigate={setCurrentView}
    >
      <AuthKeyModal
        isOpen={showAuthModal}
        onSuccess={handleAuthSuccess}
      />

      <AlertDialog open={rateLimitAlertOpen} onOpenChange={setRateLimitAlertOpen}>
        <AlertDialogContent className="border-red-500/50">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-red-500 flex items-center gap-2">
              <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-alert-octagon"><polygon points="7.86 2 16.14 2 22 7.86 22 16.14 16.14 22 7.86 22 2 16.14 2 7.86 7.86 2" /></svg>
              Rate Limit Exceeded
            </AlertDialogTitle>
            <AlertDialogDescription className="space-y-2">
              <p>The AI service is currently unavailable due to high usage (Quota Exceeded).</p>
              <div className="bg-muted p-4 rounded-md text-sm font-mono text-muted-foreground">
                Detailed error logs have been output to the terminal.
              </div>
              <p className="text-xs text-muted-foreground">Please wait a few moments before trying again, or check your API billing status.</p>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => setRateLimitAlertOpen(false)}>Okay</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>


      {
        currentView === 'instances' ? (
          <InstancesList currentSessionId={sessionId} />
        ) : currentView === 'cron' ? (
          <CronPage />
        ) : currentView === 'memory' ? (
          <MemoryPage />
        ) : currentView === 'overview' ? (
          <OverviewPage />
        ) : currentView === 'channels' ? (
          <ChannelsPage />
        ) : currentView === 'logs' ? (
          <LogsPage />
        ) : currentView === 'skills' ? (
          <SkillsPage />
        ) : currentView === 'persona' ? (
          <PersonaPage />
        ) : currentView === 'appearance' ? (
          <AppearancePage onThemeChange={handleThemeChange} />
        ) : currentView === 'config' ? (
          <ConfigPage />
        ) : (
          <ChatInterface
            messages={messages}
            inputValue={inputValue}
            isConnected={isConnected}
            isTyping={isTyping}
            botIdentity={botIdentity}
            activeChatId={sessionId}
            autonomousMode={autonomousMode}
            onInputChange={setInputValue}
            onSendMessage={handleSendMessage}
            onReconnect={connectWebSocket}
            onNewChat={handleNewChat}
          />
        )
      }

      <GhostActivity activity={activity} />
    </AppLayout >
  );

}

export default App;
