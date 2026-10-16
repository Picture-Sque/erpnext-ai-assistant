import { useCallback, useEffect, useRef, useState } from "react";
import { ChatMessage, ConnectionState, ConversationSummary } from "../types/chat";

// Configure the backend URL using environment variable VITE_BACKEND_URL or defaulting to http://localhost:8000/chat
const BACKEND_URL = (import.meta.env.VITE_BACKEND_URL as string) || "http://localhost:8000/chat";
const API_BASE = BACKEND_URL.replace(/\/chat\/?$/, "");

const ACTIVE_CONVERSATION_KEY = "ai_assistant.activeConversation";

const createId = () =>
    globalThis.crypto?.randomUUID?.() ??
    `message-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const formatTime = (date: Date) =>
    new Intl.DateTimeFormat("en-US", {
        hour: "numeric",
        minute: "2-digit"
    }).format(date);

const createTimestamp = () => formatTime(new Date());

// The backend stores naive UTC ISO timestamps.
export const parseServerDate = (value: string) => new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);

// Remembers the open conversation per user so reopening the widget returns to it.
// Browser storage can be unavailable (private mode, blocked site data); the widget works without it.
const readActiveConversation = (user: string): string | null => {
    try {
        return window.localStorage.getItem(`${ACTIVE_CONVERSATION_KEY}.${user}`);
    } catch {
        return null;
    }
};

const writeActiveConversation = (user: string, conversationId: string | null) => {
    try {
        const key = `${ACTIVE_CONVERSATION_KEY}.${user}`;
        if (conversationId) window.localStorage.setItem(key, conversationId);
        else window.localStorage.removeItem(key);
    } catch {
        // Storage unavailable: the active chat is simply not remembered across reloads.
    }
};

// Safe Frappe resolver supporting window, parent frame, and top window (handles Desk iframe context)
const getFrappe = () => {
    if (typeof window === "undefined") return null;
    return (window as any).frappe || (window as any).parent?.frappe || (window as any).top?.frappe || null;
};

// Helper to resolve Frappe session user across standard, cookie, and iframe contexts
const getFrappeSessionUser = () => {
    const frappe = getFrappe();
    let user = "";
    let fullName = "";

    if (frappe) {
        user = frappe.session?.user || frappe.boot?.user?.name || frappe.boot?.user?.email || frappe.user?.name || "";
        fullName =
            frappe.boot?.user?.full_name ||
            frappe.session?.user_fullname ||
            frappe.user_info?.[user]?.full_name ||
            frappe.user_info?.[user]?.fullname ||
            "";
    }

    // Fallback: check document.cookie for user_id and full_name
    if (typeof document !== "undefined") {
        if (!user || user === "Guest") {
            const cookieUser = document.cookie.match(/(?:^|; )user_id=([^;]*)/);
            if (cookieUser && cookieUser[1]) {
                const decodedUser = decodeURIComponent(cookieUser[1]);
                if (decodedUser && decodedUser !== "Guest") {
                    user = decodedUser;
                }
            }
        }
        if (!fullName || fullName === "Guest") {
            const cookieName = document.cookie.match(/(?:^|; )full_name=([^;]*)/);
            if (cookieName && cookieName[1]) {
                const decodedName = decodeURIComponent(cookieName[1]);
                if (decodedName && decodedName !== "Guest") {
                    fullName = decodedName;
                }
            }
        }
    }

    if (user && user !== "Guest") {
        return { username: user, fullName: fullName || user };
    }

    // Default to Guest if no user session is detected
    return { username: "Guest", fullName: "Guest" };
};

interface AuthUser {
    username: string;
    fullName: string;
    roles: string[];
    token: string;
}

const GUEST: AuthUser = { username: "Guest", fullName: "Guest", roles: [], token: "" };

// Requests a JWT for the logged-in Desk user. Resolves to null when there is no session.
const requestChatToken = (): Promise<AuthUser | null> =>
    new Promise(resolve => {
        const frappe = getFrappe();
        if (!frappe || !frappe.call) {
            console.warn("[Chat Widget] frappe.call is not available. Running outside ERPNext Desk.");
            resolve(null);
            return;
        }

        const sessionUser = getFrappeSessionUser();

        frappe.call({
            method: "ai_assistant.ai_assistant.api.get_chat_token",
            callback: (r: any) => {
                if (!r || !r.message || !r.message.token) {
                    console.warn("[Chat Widget] Token request returned an empty or unauthenticated response.");
                    resolve(null);
                    return;
                }
                const rawUser = r.message.user;
                const rawFullName = r.message.full_name;
                const resolvedUser = (rawUser && rawUser !== "Guest")
                    ? rawUser
                    : (sessionUser.username !== "Guest" ? sessionUser.username : "Guest");
                const resolvedFullName = (rawFullName && rawFullName !== "Guest")
                    ? rawFullName
                    : (resolvedUser !== "Guest" ? resolvedUser : "Guest");

                resolve({
                    username: resolvedUser,
                    fullName: resolvedFullName,
                    roles: r.message.roles || [],
                    token: r.message.token
                });
            },
            error: (err: any) => {
                console.error("[Chat Widget] Error fetching chat token:", err);
                resolve(null);
            }
        });
    });

const describeFetchError = (error: any) => {
    if (error?.message?.includes("Failed to fetch") || error?.name === "TypeError") {
        return `Unable to reach the assistant backend at ${API_BASE}. Make sure the server is running.`;
    }
    return error?.message || String(error);
};

const toSummary = (raw: any): ConversationSummary => ({
    id: raw.id,
    title: raw.title,
    createdAt: raw.created_at,
    updatedAt: raw.updated_at
});

export function useDemoChat() {
    const initialUser = getFrappeSessionUser();

    const [authUser, setAuthUser] = useState<AuthUser>({ ...GUEST, ...initialUser });
    const [connection, setConnection] = useState<ConnectionState>("connecting");
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [conversationId, setConversationId] = useState<string>(() => createId());
    const [conversations, setConversations] = useState<ConversationSummary[]>([]);
    const [historyStatus, setHistoryStatus] = useState<"idle" | "loading" | "error">("idle");
    const [isLoadingConversation, setIsLoadingConversation] = useState(false);
    const [sendingConversationId, setSendingConversationId] = useState<string | null>(null);

    const isMountedRef = useRef(true);
    const tokenRef = useRef<string>("");
    const conversationIdRef = useRef(conversationId);
    const restoredForUserRef = useRef<string | null>(null);

    useEffect(() => {
        conversationIdRef.current = conversationId;
    }, [conversationId]);

    const authenticate = useCallback(async () => {
        const user = await requestChatToken();
        if (!isMountedRef.current) return "";
        if (!user) {
            tokenRef.current = "";
            setAuthUser(prev => (prev.token ? prev : { ...GUEST, ...getFrappeSessionUser() }));
            setConnection(prev => (tokenRef.current ? prev : "signed-out"));
            return "";
        }
        tokenRef.current = user.token;
        setAuthUser(user);
        setConnection("connected");
        return user.token;
    }, []);

    // Sends an authenticated request to the backend, refreshing the token once on 401.
    const apiFetch = useCallback(
        async (path: string, init: RequestInit = {}) => {
            const send = (token: string) =>
                fetch(`${API_BASE}${path}`, {
                    ...init,
                    headers: {
                        "Content-Type": "application/json",
                        ...(token ? { Authorization: `Bearer ${token}` } : {}),
                        ...(init.headers || {})
                    }
                });

            let token = tokenRef.current || (await authenticate());
            let response = await send(token);

            if (response.status === 401) {
                console.warn("[Chat Widget] Received 401 Unauthorized. Refreshing the token and retrying once.");
                token = await authenticate();
                if (token) response = await send(token);
            }
            if (response.status === 401) {
                throw new Error("Your session could not be verified. Reload ERPNext and sign in again.");
            }
            if (!response.ok) {
                let detail = response.statusText;
                try {
                    const body = await response.json();
                    detail = body?.detail || detail;
                } catch {
                    // Body was not JSON; keep the status text.
                }
                throw new Error(`HTTP ${response.status}: ${detail}`);
            }
            return response.json();
        },
        [authenticate]
    );

    const refreshConversations = useCallback(async () => {
        if (!tokenRef.current) return;
        setHistoryStatus(prev => (prev === "error" ? "loading" : prev));
        try {
            const data = await apiFetch("/conversations");
            if (!isMountedRef.current) return;
            setConversations((data.conversations || []).map(toSummary));
            setHistoryStatus("idle");
        } catch (error) {
            console.error("[Chat Widget] Could not load chat history:", error);
            if (isMountedRef.current) setHistoryStatus("error");
        }
    }, [apiFetch]);

    const loadConversation = useCallback(
        async (id: string) => {
            setConversationId(id);
            conversationIdRef.current = id;
            setMessages([]);
            setIsLoadingConversation(true);
            writeActiveConversation(authUser.username, id);
            try {
                const data = await apiFetch(`/conversations/${encodeURIComponent(id)}/messages`);
                if (!isMountedRef.current || conversationIdRef.current !== id) return;
                setMessages(
                    (data.messages || []).map((m: any) => ({
                        id: `server-${m.id}`,
                        role: m.role === "user" ? "user" : "assistant",
                        content: m.content,
                        timestamp: formatTime(parseServerDate(m.created_at))
                    }))
                );
            } catch (error) {
                if (!isMountedRef.current || conversationIdRef.current !== id) return;
                setMessages([
                    {
                        id: createId(),
                        role: "assistant",
                        content: `Could not load this conversation. ${describeFetchError(error)}`,
                        timestamp: createTimestamp(),
                        isError: true
                    }
                ]);
            } finally {
                if (isMountedRef.current && conversationIdRef.current === id) setIsLoadingConversation(false);
            }
        },
        [apiFetch, authUser.username]
    );

    // Initial sign-in, retried once Frappe reports ready.
    useEffect(() => {
        isMountedRef.current = true;
        authenticate();

        const frappe = getFrappe();
        let retryTimer: ReturnType<typeof setTimeout> | null = null;
        if (frappe && frappe.ready) {
            frappe.ready(() => {
                if (!tokenRef.current) authenticate();
            });
        } else {
            retryTimer = setTimeout(() => {
                if (!tokenRef.current) authenticate();
            }, 1000);
        }

        return () => {
            isMountedRef.current = false;
            if (retryTimer !== null) clearTimeout(retryTimer);
        };
    }, [authenticate]);

    // Once signed in: load the history and reopen the chat the user had open last.
    useEffect(() => {
        if (connection !== "connected" || restoredForUserRef.current === authUser.username) return;
        restoredForUserRef.current = authUser.username;

        setHistoryStatus("loading");
        refreshConversations();

        const lastActive = readActiveConversation(authUser.username);
        if (lastActive) loadConversation(lastActive);
    }, [authUser.username, connection, loadConversation, refreshConversations]);

    const sendMessage = useCallback(
        async (rawMessage: string) => {
            const content = rawMessage.trim();
            if (!content || sendingConversationId) return false;

            const targetConversation = conversationIdRef.current;
            const isFirstMessage = !conversations.some(c => c.id === targetConversation);

            setMessages(prev => [
                ...prev,
                { id: createId(), role: "user", content, timestamp: createTimestamp() }
            ]);
            setSendingConversationId(targetConversation);
            writeActiveConversation(authUser.username, targetConversation);

            if (isFirstMessage) {
                const now = new Date().toISOString();
                setConversations(prev => [
                    { id: targetConversation, title: content.replace(/\s+/g, " ").slice(0, 60), createdAt: now, updatedAt: now },
                    ...prev
                ]);
            }

            let reply: ChatMessage;
            try {
                const data = await apiFetch("/chat", {
                    method: "POST",
                    body: JSON.stringify({ message: content, conversation_id: targetConversation })
                });
                reply = {
                    id: createId(),
                    role: "assistant",
                    content: data.response || "The assistant returned an empty response.",
                    timestamp: createTimestamp()
                };
            } catch (error: any) {
                console.error("[Chat Widget] Error communicating with the backend:", error);
                reply = {
                    id: createId(),
                    role: "assistant",
                    content: describeFetchError(error),
                    timestamp: createTimestamp(),
                    isError: true
                };
            }

            if (!isMountedRef.current) return true;
            setSendingConversationId(null);
            // The user may have switched chats while waiting; the reply is saved server-side either way.
            if (conversationIdRef.current === targetConversation) {
                setMessages(prev => [...prev, reply]);
            }
            refreshConversations();
            return true;
        },
        [apiFetch, authUser.username, conversations, refreshConversations, sendingConversationId]
    );

    const startNewChat = useCallback(() => {
        const id = createId();
        conversationIdRef.current = id;
        setConversationId(id);
        setMessages([]);
        setIsLoadingConversation(false);
        writeActiveConversation(authUser.username, null);
    }, [authUser.username]);

    const deleteConversation = useCallback(
        async (id: string) => {
            try {
                await apiFetch(`/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
            } catch (error) {
                console.error("[Chat Widget] Could not delete conversation:", error);
                window.alert(`Could not delete the chat. ${describeFetchError(error)}`);
                return false;
            }
            setConversations(prev => prev.filter(c => c.id !== id));
            if (conversationIdRef.current === id) startNewChat();
            return true;
        },
        [apiFetch, startNewChat]
    );

    // Optimistic: the new title shows at once and is restored if the server refuses it.
    const renameConversation = useCallback(
        async (id: string, rawTitle: string) => {
            const title = rawTitle.replace(/\s+/g, " ").trim();
            const previous = conversations.find(c => c.id === id)?.title;
            if (!title || title === previous) return true;

            setConversations(prev => prev.map(c => (c.id === id ? { ...c, title } : c)));
            try {
                await apiFetch(`/conversations/${encodeURIComponent(id)}`, {
                    method: "PATCH",
                    body: JSON.stringify({ title })
                });
                return true;
            } catch (error) {
                console.error("[Chat Widget] Could not rename conversation:", error);
                if (previous !== undefined && isMountedRef.current) {
                    setConversations(prev => prev.map(c => (c.id === id ? { ...c, title: previous } : c)));
                }
                window.alert(`Could not rename the chat. ${describeFetchError(error)}`);
                return false;
            }
        },
        [apiFetch, conversations]
    );

    return {
        authUser,
        connection,
        currentUser: authUser.fullName,
        messages,
        conversationId,
        conversations,
        historyStatus,
        isLoadingConversation,
        isSending: sendingConversationId !== null,
        isSendingHere: sendingConversationId !== null && sendingConversationId === conversationId,
        sendMessage,
        startNewChat,
        loadConversation,
        deleteConversation,
        renameConversation,
        refreshConversations
    };
}
