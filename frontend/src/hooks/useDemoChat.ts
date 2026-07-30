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