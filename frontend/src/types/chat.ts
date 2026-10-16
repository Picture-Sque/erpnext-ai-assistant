export type ChatRole = "assistant" | "user";

export interface ChatMessage {
	id: string;
	role: ChatRole;
	content: string;
	timestamp: string;
	isError?: boolean;
}

export interface ConversationSummary {
	id: string;
	title: string;
	createdAt: string;
	updatedAt: string;
}

export type ConnectionState = "connecting" | "connected" | "signed-out";
