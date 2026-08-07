import { ChatLogoIcon, CloseIcon } from "./chat-icons";

interface ChatHeaderProps {
	onClose?: () => void;
	currentUser?: string;
}

export function ChatHeader({ onClose, currentUser }: ChatHeaderProps) {
	const displayUser = currentUser && currentUser.trim() !== "" ? currentUser : "Guest";

	return (
		<header className="chat-header">
			<div className="chat-header__brand">
				<div className="chat-header__mark">
					<ChatLogoIcon className="chat-header__mark-icon" size={18} />
				</div>

				<div>
					<p className="chat-header__eyebrow">AI Assistant</p>
					<h1 className="chat-header__title">Enterprise assistant</h1>
					<p className="chat-header__subtitle">Connected to ERPNext</p>
				</div>
			</div>

			<div className="chat-header__meta">
				<div className="chat-header__status">
					<span className="chat-header__status-dot" />
					<span>Connected</span>
				</div>

				<div className="chat-header__user-slot">
					<span className="chat-header__user-label">ERP User</span>
					<span className="chat-header__user-value">{displayUser}</span>
				</div>

				{onClose ? (
					<button
						className="chat-header__close"
						type="button"
						onClick={onClose}
						aria-label="Close AI assistant sidebar"
					>
						<CloseIcon />
					</button>
				) : null}
			</div>
		</header>
	);
}