import { useCallback, useEffect, useRef, useState } from "react";
import { ChatMessage } from "../types/chat";

// Configure the backend URL using environment variable VITE_BACKEND_URL or defaulting to http://localhost:8000/chat
const BACKEND_URL = (import.meta.env.VITE_BACKEND_URL as string) || "http://localhost:8000/chat";

const createId = () =>
    globalThis.crypto?.randomUUID?.() ??
    `message-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const createTimestamp = () =>
    new Intl.DateTimeFormat("en-US", {
        hour: "numeric",
        minute: "2-digit"
    }).format(new Date());

// Safe Frappe resolver supporting window, parent frame, and top window (handles Desk iframe context)
const getFrappe = () => {
    if (typeof window === "undefined") return null;
    return (window as any).frappe || (window as any).parent?.frappe || (window as any).top?.frappe || null;
};

// Utility to parse human-readable messages out of complex Frappe error objects
const extractFrappeErrorMessage = (err: any): string => {
    if (!err) return "Unknown authentication error";
    if (typeof err === "string") return err;
    if (err._server_messages) {
        try {
            const messages = JSON.parse(err._server_messages);
            const parsed = messages
                .map((m: string) => {
                    try {
                        return JSON.parse(m).message;
                    } catch {
                        return m;
                    }
                })
                .filter(Boolean)
                .join("; ");
            if (parsed) return parsed;
        } catch {
            // fallback to stringifying
        }
    }
    if (err.message) return err.message;
    if (err.exception) return err.exception;
    return JSON.stringify(err);
};

export function useDemoChat() {
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

    const initialUser = getFrappeSessionUser();

    const [authUser, setAuthUser] = useState<{
        username: string;
        fullName: string;
        roles: string[];
        token: string;
    }>({
        username: initialUser.username,
        fullName: initialUser.fullName,
        roles: [],
        token: "",
    });

    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const isMountedRef = useRef(true);
    const tokenRef = useRef<string>("");

    // Sync tokenRef whenever authUser.token changes (avoids stale closure in sendMessage)
    useEffect(() => {
        tokenRef.current = authUser.token;
    }, [authUser.token]);

    // Helper promise to fetch token directly on-demand if missing or expired
    const fetchTokenOnDemand = (): Promise<string> => {
        return new Promise((resolve) => {
            const frappe = getFrappe();
            if (!frappe || !frappe.call) {
                console.warn("[Chat Widget] Cannot fetch token on-demand: frappe.call unavailable.");
                resolve("");
                return;
            }

            const currentSession = getFrappeSessionUser();

            console.log("[Chat Widget] Fetching fresh token on-demand from Frappe...");
            frappe.call({
                method: "ai_assistant.ai_assistant.api.get_chat_token",
                callback: (r: any) => {
                    if (r && r.message && r.message.token) {
                        const rawUser = r.message.user;
                        const rawFullName = r.message.full_name;
                        const resolvedUser = (rawUser && rawUser !== "Guest")
                            ? rawUser
                            : (currentSession.username !== "Guest" ? currentSession.username : "Guest");
                        const resolvedFullName = (rawFullName && rawFullName !== "Guest")
                            ? rawFullName
                            : (resolvedUser !== "Guest" ? resolvedUser : "Guest");

                        const userObj = {
                            username: resolvedUser,
                            fullName: resolvedFullName,
                            roles: r.message.roles || [],
                            token: r.message.token,
                        };
                        tokenRef.current = userObj.token;
                        setAuthUser(userObj);
                        resolve(userObj.token);
                    } else {
                        console.warn("[Chat Widget] On-demand token request returned empty token.");
                        resolve("");
                    }
                },
                error: (err: any) => {
                    console.error("[Chat Widget] Error fetching token on-demand:", err);
                    resolve("");
                }
            });
        });
    };

    const authenticateWithFrappe = useCallback(() => {
        const frappe = getFrappe();

        // Step 1: Immediately read session values if available
        const sessionUser = getFrappeSessionUser();
        console.log("[Chat Widget] Direct session read:", sessionUser);
        if (sessionUser.username !== "Guest") {
            setAuthUser(prev => ({
                ...prev,
                username: sessionUser.username,
                fullName: sessionUser.fullName,
            }));
        } else {
            setAuthUser({
                username: "Guest",
                fullName: "Guest",
                roles: [],
                token: "",
            });
        }

        // Step 2: Fetch JWT token via frappe.call
        if (frappe && frappe.call) {
            console.log("[Chat Widget] Calling ai_assistant.ai_assistant.api.get_chat_token");
            frappe.call({
                method: "ai_assistant.ai_assistant.api.get_chat_token",
                callback: (r: any) => {
                    console.log("[Chat Widget] frappe.call callback response:", r);
                    if (r && r.message && r.message.token) {
                        console.log("[Chat Widget] Successfully authenticated user:", r.message.user);
                        const rawUser = r.message.user;
                        const rawFullName = r.message.full_name;
                        const resolvedUser = (rawUser && rawUser !== "Guest")
                            ? rawUser
                            : (sessionUser.username !== "Guest" ? sessionUser.username : "Guest");
                        const resolvedFullName = (rawFullName && rawFullName !== "Guest")
                            ? rawFullName
                            : (resolvedUser !== "Guest" ? resolvedUser : "Guest");

                        const userObj = {
                            username: resolvedUser,
                            fullName: resolvedFullName,
                            roles: r.message.roles || [],
                            token: r.message.token || "",
                        };
                        tokenRef.current = userObj.token;
                        setAuthUser(userObj);

                        if (isMountedRef.current) {
                            setMessages(prev => {
                                if (prev.length === 0) {
                                    return [
                                        {
                                            id: createId(),
                                            role: "assistant",
                                            content: `Welcome, ${userObj.fullName}! Connected to ERPNext. How can I assist you today?`,
                                            timestamp: createTimestamp()
                                        }
                                    ];
                                }
                                return prev;
                            });
                        }
                    } else {
                        console.warn("[Chat Widget] frappe.call returned empty or unauthenticated response.");
                        setAuthUser({
                            username: "Guest",
                            fullName: "Guest",
                            roles: [],
                            token: "",
                        });
                    }
                },
                error: (err: any) => {
                    console.error("[Chat Widget] frappe.call error fetching token:", err);
                    setAuthUser({
                        username: "Guest",
                        fullName: "Guest",
                        roles: [],
                        token: "",
                    });
                }
            });
        } else {
            console.warn("[Chat Widget] frappe.call is not available. Running outside ERPNext Desk.");
            if (isMountedRef.current) {
                setMessages(prev => {
                    if (prev.length === 0) {
                        return [
                            {
                                id: createId(),
                                role: "assistant",
                                content: "Running in standalone mode. Log into ERPNext Desk to enable user identity sync.",
                                timestamp: createTimestamp()
                            }
                        ];
                    }
                    return prev;
                });
            }
        }
    }, []);

    useEffect(() => {
        isMountedRef.current = true;
        let cleanupTimer: ReturnType<typeof setTimeout> | null = null;

        authenticateWithFrappe();

        const frappe = getFrappe();
        if (frappe && frappe.ready) {
            frappe.ready(() => {
                authenticateWithFrappe();
            });
        } else {
            cleanupTimer = setTimeout(() => {
                authenticateWithFrappe();
            }, 1000);
        }

        return () => {
            isMountedRef.current = false;
            if (cleanupTimer !== null) {
                clearTimeout(cleanupTimer);
            }
        };
    }, [authenticateWithFrappe]);

    const sendMessage = useCallback(
        async (rawMessage: string) => {
            const content = rawMessage.trim();
            if (!content) return false;

            const userMessage: ChatMessage = {
                id: createId(),
                role: "user",
                content,
                timestamp: createTimestamp()
            };

            setMessages(prev => [...prev, userMessage]);

            // Read token from ref or state
            let activeToken = tokenRef.current || authUser.token;

            // If activeToken is missing, fetch it on-demand before executing fetch
            if (!activeToken) {
                console.log("[Chat Widget] Token empty during sendMessage, fetching on-demand...");
                activeToken = await fetchTokenOnDemand();
            }

            const sendHttpRequest = async (tokenToUse: string): Promise<Response> => {
                const headers: HeadersInit = {
                    "Content-Type": "application/json"
                };
                if (tokenToUse) {
                    headers["Authorization"] = `Bearer ${tokenToUse}`;
                }
                return fetch(BACKEND_URL, {
                    method: "POST",
                    headers,
                    body: JSON.stringify({ message: content })
                });
            };

            try {
                let response = await sendHttpRequest(activeToken);

                // If 401 Unauthorized occurs, clear token and attempt single automatic refresh retry
                if (response.status === 401) {
                    console.warn("[Chat Widget] Received 401 Unauthorized. Attempting automatic token refresh...");
                    const freshToken = await fetchTokenOnDemand();
                    if (freshToken) {
                        console.log("[Chat Widget] Retrying chat request with fresh token...");
                        response = await sendHttpRequest(freshToken);
                    }
                }

                if (response.status === 401) {
                    throw new Error("401 Unauthorized: JWT token missing, expired, or invalid signature.");
                }
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }

                const data = await response.json();
                const replyText = data.response || "No response field returned from backend.";

                if (isMountedRef.current) {
                    setMessages(prev => [
                        ...prev,
                        {
                            id: createId(),
                            role: "assistant",
                            content: replyText,
                            timestamp: createTimestamp()
                        }
                    ]);
                }
            } catch (error: any) {
                console.error("[Chat Widget] Error communicating with FastAPI backend:", error);
                let errorNotice = `Error: ${error.message}`;
                if (error.message?.includes("Failed to fetch") || error.name === "TypeError") {
                    errorNotice = "Error: Unable to connect to the FastAPI backend server (Connection Refused). Ensure 'python main.py' is running at http://localhost:8000.";
                }

                if (isMountedRef.current) {
                    setMessages(prev => [
                        ...prev,
                        {
                            id: createId(),
                            role: "assistant",
                            content: errorNotice,
                            timestamp: createTimestamp()
                        }
                    ]);
                }
            }

            return true;
        },
        [authUser.token]
    );

    return {
        authUser,
        setAuthUser,
        currentUser: authUser.fullName,
        messages,
        sendMessage
    };
}