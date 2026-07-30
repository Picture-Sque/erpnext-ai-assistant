import { ChatLogoIcon } from "./chat-icons";

export function ChatHeader() {
	return (
		<header className="chat-header">
			<div className="chat-header__brand">
				<div className="chat-header__mark">
					<ChatLogoIcon className="chat-header__mark-icon" size={18} />
				</div>

				<div>
					<p className="chat-header__eyebrow">AI Assistant</p>
					<h1 className="chat-header__title">Chatbot UI-inspired Demo</h1>
				</div>
			</div>

			<div className="chat-header__status">
				<span className="chat-header__status-dot" />
				<span>Static local messages</span>
			</div>
		</header>
	);
}