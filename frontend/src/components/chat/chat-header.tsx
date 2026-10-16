import { CloseIcon, NewChatIcon, SidebarIcon } from "./chat-icons";

interface ChatHeaderProps {
	title: string;
	isSidebarOpen: boolean;
	onOpenSidebar: () => void;
	onNewChat: () => void;
	onClose?: () => void;
}

export function ChatHeader({ title, isSidebarOpen, onOpenSidebar, onNewChat, onClose }: ChatHeaderProps) {
	return (
		<header className="chat-topbar">
			<div className="chat-topbar__left">
				{!isSidebarOpen ? (
					<>
						<button
							className="chat-icon-btn"
							type="button"
							onClick={onOpenSidebar}
							aria-label="Open sidebar"
							title="Open sidebar"
						>
							<SidebarIcon />
						</button>
						<button
							className="chat-icon-btn"
							type="button"
							onClick={onNewChat}
							aria-label="New chat"
							title="New chat"
						>
							<NewChatIcon />
						</button>
					</>
				) : null}
				<h1 className="chat-topbar__title">{title}</h1>
			</div>

			{onClose ? (
				<button
					className="chat-icon-btn"
					type="button"
					onClick={onClose}
					aria-label="Close AI assistant"
					title="Close (Esc)"
				>
					<CloseIcon size={18} />
				</button>
			) : null}
		</header>
	);
}
