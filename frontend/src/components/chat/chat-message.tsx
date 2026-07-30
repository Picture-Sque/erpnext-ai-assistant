import { ChatMessage } from "../../types/chat";
import { UserCircleIcon } from "./chat-icons";

interface ChatMessageProps {
	message: ChatMessage;
}

export function ChatMessageView({ message }: ChatMessageProps) {
	const isAssistant = message.role === "assistant";

	return (
		<article className={`chat-message chat-message--${message.role}`}>
			<div className="chat-message__avatar">
				{isAssistant ? (
					<div className="chat-message__avatar-mark" aria-hidden="true">
						<ChatbotBubbleMark />
					</div>
				) : (
					<div className="chat-message__avatar-user" aria-hidden="true">
						<UserCircleIcon className="chat-message__user-icon" />
					</div>
				)}
			</div>

			<div className="chat-message__body">
				<div className="chat-message__meta">
					<span className="chat-message__role">{isAssistant ? "Assistant" : "You"}</span>
					<span className="chat-message__timestamp">{message.timestamp}</span>
				</div>

				<div className="chat-message__bubble">
					{message.content.split("\n").map((line, index) => (
						<p key={`${message.id}-${index}`}>{line}</p>
					))}
				</div>
			</div>
		</article>
	);
}

function ChatbotBubbleMark() {
	return (
		<svg aria-hidden="true" fill="none" height="16" viewBox="0 0 24 24" width="16">
			<path d="M6.75 4.5h10.5a3.75 3.75 0 0 1 3.75 3.75v2.25a3.75 3.75 0 0 1-3.75 3.75H12l-4.5 3v-3H6.75A3.75 3.75 0 0 1 3 10.5V8.25A3.75 3.75 0 0 1 6.75 4.5Z" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.7" />
			<path d="M8.75 9.75h.01M12 9.75h.01M15.25 9.75h.01" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.2" />
		</svg>
	);
}