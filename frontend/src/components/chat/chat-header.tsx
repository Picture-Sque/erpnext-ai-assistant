import { ChatLogoIcon, CloseIcon, MaximizeIcon, MinimizeIcon, PlusIcon } from "./chat-icons";

interface ChatHeaderProps {
	onClose?: () => void;
	currentUser?: string;
	onNewChat?: () => void;
	isExpanded?: boolean;
	onToggleExpand?: () => void;
}

export function ChatHeader({
	onClose,
	currentUser,
	onNewChat,
	isExpanded = false,
	onToggleExpand
}: ChatHeaderProps) {
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
				<div className="chat-header__actions">
					{onNewChat ? (
						<button
							className="chat-header__btn chat-header__btn--new"
							type="button"
							onClick={onNewChat}
							aria-label="Start new chat session"
							title="New chat"
						>
							<PlusIcon size={15} />
							<span>New Chat</span>
						</button>
					) : null}

					{onToggleExpand ? (
						<button
							className="chat-header__btn chat-header__btn--icon"
							type="button"
							onClick={onToggleExpand}
							aria-label={isExpanded ? "Collapse to sidebar" : "Expand to full window"}
							title={isExpanded ? "Collapse to sidebar" : "Full window"}
						>
							{isExpanded ? <MinimizeIcon size={15} /> : <MaximizeIcon size={15} />}
						</button>
					) : null}

					{onClose ? (
						<button
							className="chat-header__close"
							type="button"
							onClick={onClose}
							aria-label="Close AI assistant"
							title="Close"
						>
							<CloseIcon />
						</button>
					) : null}
				</div>

				<div className="chat-header__badges">
					<div className="chat-header__status">
						<span className="chat-header__status-dot" />
						<span>Connected</span>
					</div>

					<div className="chat-header__user-slot">
						<span className="chat-header__user-label">ERP User</span>
						<span className="chat-header__user-value">{displayUser}</span>
					</div>
				</div>
			</div>
		</header>
	);
}