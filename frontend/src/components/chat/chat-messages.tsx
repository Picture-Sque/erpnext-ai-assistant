import { ChatMessage } from "../../types/chat";
import { ChatMessageView, ChatTypingIndicator } from "./chat-message";

interface ChatMessagesProps {
	messages: ChatMessage[];
	isThinking?: boolean;
}

export function ChatMessages({ messages, isThinking = false }: ChatMessagesProps) {
	return (
		<div className="chat-messages">
			{messages.map(message => (
				<ChatMessageView key={message.id} message={message} />
			))}
			{isThinking ? <ChatTypingIndicator /> : null}
		</div>
	);
}
