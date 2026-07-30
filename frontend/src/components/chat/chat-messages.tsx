import { ChatMessage } from "../../types/chat";
import { ChatMessageView } from "./chat-message";

interface ChatMessagesProps {
	messages: ChatMessage[];
}

export function ChatMessages({ messages }: ChatMessagesProps) {
	return (
		<div className="chat-messages">
			{messages.map(message => (
				<ChatMessageView key={message.id} message={message} />
			))}
		</div>
	);
}