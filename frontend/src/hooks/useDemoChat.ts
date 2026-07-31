import { useCallback, useEffect, useRef, useState } from "react";
import { ChatMessage } from "../types/chat";

// Authorization Bearer token placeholder. Customize this as needed.
export const AUTH_TOKEN = "";

// Configure the backend URL using environment variable VITE_BACKEND_URL or defaulting to localhost.
const BACKEND_URL = (import.meta.env.VITE_BACKEND_URL as string) || "http://localhost:8000/chat";

const createId = () =>
	globalThis.crypto?.randomUUID?.() ??
	`message-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const createTimestamp = () =>
	new Intl.DateTimeFormat("en-US", {
		hour: "numeric",
		minute: "2-digit"
	}).format(new Date());

const initialMessages: ChatMessage[] = [
	{
		id: createId(),
		role: "assistant",
		content:
			"Welcome. I can help with ERPNext workflows, operational questions, and account-specific guidance once connected.",
		timestamp: createTimestamp()
	},
	{
		id: createId(),
		role: "user",
		content: "Show me a quick summary of today’s sales pipeline.",
		timestamp: createTimestamp()
	},
	{
		id: createId(),
		role: "assistant",
		content:
			"I can surface a concise summary, highlight exceptions, and keep the conversation focused on business context.",
		timestamp: createTimestamp()
	}
];

export const useDemoChat = () => {
	const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
	const isMountedRef = useRef(true);

	useEffect(() => {
		isMountedRef.current = true;
		return () => {
			isMountedRef.current = false;
		};
	}, []);

	const sendMessage = useCallback((rawMessage: string) => {
		const content = rawMessage.trim();

		if (!content) return false;

		const userMessage: ChatMessage = {
			id: createId(),
			role: "user",
			content,
			timestamp: createTimestamp()
		};

		// Append user's message immediately
		setMessages(prevMessages => [...prevMessages, userMessage]);

		// Prepare headers
		const headers: HeadersInit = {
			"Content-Type": "application/json"
		};
		if (AUTH_TOKEN) {
			headers["Authorization"] = `Bearer ${AUTH_TOKEN}`;
		}

		// Perform API request
		fetch(BACKEND_URL, {
			method: "POST",
			headers,
			body: JSON.stringify({ message: content })
		})
			.then(async response => {
				if (!response.ok) {
					throw new Error(`HTTP error! Status: ${response.status}`);
				}
				const data = await response.json();
				const replyText = data.response || "No response field returned from backend.";

				if (isMountedRef.current) {
					setMessages(prevMessages => [
						...prevMessages,
						{
							id: createId(),
							role: "assistant",
							content: replyText,
							timestamp: createTimestamp()
						}
					]);
				}
			})
			.catch(error => {
				console.error("Error communicating with chat backend:", error);
				if (isMountedRef.current) {
					setMessages(prevMessages => [
						...prevMessages,
						{
							id: createId(),
							role: "assistant",
							content: `Error: Unable to connect to the backend server (${error.message}). Please check that your server is running at ${BACKEND_URL}.`,
							timestamp: createTimestamp()
						}
					]);
				}
			});

		return true;
	}, []);

	return {
		messages,
		sendMessage
	};
};