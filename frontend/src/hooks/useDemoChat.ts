import { useCallback, useEffect, useRef, useState } from "react";
import { ChatMessage } from "../types/chat";

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
			"Welcome to the Chatbot UI-inspired demo. This is a static local chat shell ready for future integration.",
		timestamp: createTimestamp()
	},
	{
		id: createId(),
		role: "user",
		content: "What can this interface do right now?",
		timestamp: createTimestamp()
	},
	{
		id: createId(),
		role: "assistant",
		content:
			"It renders a polished chat window, supports local message entry, and returns canned assistant responses without touching any backend.",
		timestamp: createTimestamp()
	}
];

const buildFakeAssistantReply = (input: string) => {
	const normalizedInput = input.trim().replace(/\s+/g, " ");
	const preview =
		normalizedInput.length > 96
			? `${normalizedInput.slice(0, 93)}...`
			: normalizedInput;

	return `Demo response: I received "${preview}". This is a local placeholder reply for the Chatbot UI integration shell.`;
};

export const useDemoChat = () => {
	const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
	const pendingTimeouts = useRef<number[]>([]);

	useEffect(() => {
		return () => {
			pendingTimeouts.current.forEach(timeoutId => {
				window.clearTimeout(timeoutId);
			});
			pendingTimeouts.current = [];
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

		const assistantReply = buildFakeAssistantReply(content);

		setMessages(prevMessages => [...prevMessages, userMessage]);

		const timeoutId = window.setTimeout(() => {
			setMessages(prevMessages => [
				...prevMessages,
				{
					id: createId(),
					role: "assistant",
					content: assistantReply,
					timestamp: createTimestamp()
				}
			]);

			pendingTimeouts.current = pendingTimeouts.current.filter(
				pendingTimeout => pendingTimeout !== timeoutId
			);
		}, 450);

		pendingTimeouts.current.push(timeoutId);

		return true;
	}, []);

	return {
		messages,
		sendMessage
	};
};