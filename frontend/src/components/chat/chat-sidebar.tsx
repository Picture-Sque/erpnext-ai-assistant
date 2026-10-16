import { KeyboardEvent, useMemo, useRef, useState } from "react";
import { parseServerDate } from "../../hooks/useDemoChat";
import { ConnectionState, ConversationSummary } from "../../types/chat";
import { ChatLogoIcon, NewChatIcon, PencilIcon, SearchIcon, SidebarIcon, TrashIcon } from "./chat-icons";

interface ChatSidebarProps {
	conversations: ConversationSummary[];
	activeConversationId: string;
	historyStatus: "idle" | "loading" | "error";
	userName: string;
	userId: string;
	connection: ConnectionState;
	onNewChat: () => void;
	onSelectConversation: (id: string) => void;
	onDeleteConversation: (id: string) => Promise<boolean>;
	onRenameConversation: (id: string, title: string) => Promise<boolean>;
	onRetryHistory: () => void;
	onCollapse: () => void;
}

const DAY_MS = 24 * 60 * 60 * 1000;

const groupLabel = (updatedAt: string, startOfToday: number) => {
	const time = parseServerDate(updatedAt).getTime();
	if (time >= startOfToday) return "Today";
	if (time >= startOfToday - DAY_MS) return "Yesterday";
	if (time >= startOfToday - 7 * DAY_MS) return "Previous 7 days";
	if (time >= startOfToday - 30 * DAY_MS) return "Previous 30 days";
	return "Older";
};

const initialsOf = (name: string) => {
	const parts = name.replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
	return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
};

const CONNECTION_LABEL: Record<ConnectionState, string> = {
	connecting: "Connecting…",
	connected: "Connected to ERPNext",
	"signed-out": "Not signed in"
};

export function ChatSidebar({
	conversations,
	activeConversationId,
	historyStatus,
	userName,
	userId,
	connection,
	onNewChat,
	onSelectConversation,
	onDeleteConversation,
	onRenameConversation,
	onRetryHistory,
	onCollapse
}: ChatSidebarProps) {
	const [query, setQuery] = useState("");
	const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
	const [renaming, setRenamingState] = useState<{ id: string; draft: string } | null>(null);
	// Mirrors `renaming` so a blur fired while the field is being removed sees the latest value.
	const renamingRef = useRef<{ id: string; draft: string } | null>(null);
	const setRenaming = (value: { id: string; draft: string } | null) => {
		renamingRef.current = value;
		setRenamingState(value);
	};

	const groups = useMemo(() => {
		const needle = query.trim().toLowerCase();
		const startOfToday = new Date().setHours(0, 0, 0, 0);
		const result: { label: string; items: ConversationSummary[] }[] = [];

		conversations
			.filter(c => !needle || c.title.toLowerCase().includes(needle))
			.forEach(conversation => {
				const label = groupLabel(conversation.updatedAt, startOfToday);
				const group = result.find(g => g.label === label);
				if (group) group.items.push(conversation);
				else result.push({ label, items: [conversation] });
			});

		return result;
	}, [conversations, query]);

	const handleDelete = async (id: string) => {
		const deleted = await onDeleteConversation(id);
		if (deleted) setConfirmingDelete(null);
	};

	const startRename = (conversation: ConversationSummary) => {
		setConfirmingDelete(null);
		setRenaming({ id: conversation.id, draft: conversation.title });
	};

	// Enter and blur both commit; clearing the edit first stops a second save, and a cancelled edit never saves.
	const commitRename = () => {
		const current = renamingRef.current;
		if (!current) return;
		const { id, draft } = current;
		setRenaming(null);
		onRenameConversation(id, draft);
	};

	const handleRenameKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
		if (event.key === "Enter") {
			event.preventDefault();
			commitRename();
		} else if (event.key === "Escape") {
			event.preventDefault();
			setRenaming(null);
		}
	};

	return (
		<nav className="chat-sidebar" aria-label="Chat history">
			<div className="chat-sidebar__top">
				<div className="chat-sidebar__brand">
					<span className="chat-sidebar__logo">
						<ChatLogoIcon size={17} />
					</span>
					<span className="chat-sidebar__brand-name">ERPNext Assistant</span>
					<button
						className="chat-icon-btn chat-sidebar__collapse"
						type="button"
						onClick={onCollapse}
						aria-label="Close sidebar"
						title="Close sidebar"
					>
						<SidebarIcon />
					</button>
				</div>

				<button className="chat-sidebar__new" type="button" onClick={onNewChat}>
					<NewChatIcon size={17} />
					<span>New chat</span>
				</button>

				<label className="chat-sidebar__search">
					<SearchIcon />
					<input
						type="search"
						value={query}
						onChange={event => setQuery(event.target.value)}
						placeholder="Search chats"
						aria-label="Search chats"
					/>
				</label>
			</div>

			<div className="chat-sidebar__history">
				{historyStatus === "loading" && conversations.length === 0 ? (
					<p className="chat-sidebar__note">Loading chats…</p>
				) : null}

				{historyStatus === "error" ? (
					<div className="chat-sidebar__note chat-sidebar__note--error">
						<span>Couldn't load your chats.</span>
						<button type="button" onClick={onRetryHistory}>Retry</button>
					</div>
				) : null}

				{historyStatus === "idle" && conversations.length === 0 ? (
					<p className="chat-sidebar__note">Your chats will appear here.</p>
				) : null}

				{query && groups.length === 0 && conversations.length > 0 ? (
					<p className="chat-sidebar__note">No chats match "{query}".</p>
				) : null}

				{groups.map(group => (
					<section className="chat-sidebar__group" key={group.label}>
						<h2 className="chat-sidebar__group-label">{group.label}</h2>
						<ul>
							{group.items.map(conversation => {
								const isActive = conversation.id === activeConversationId;
								const isConfirming = confirmingDelete === conversation.id;
								const isRenaming = renaming?.id === conversation.id;

								return (
									<li
										key={conversation.id}
										className="chat-sidebar__item"
										data-active={isActive}
										data-confirming={isConfirming}
										data-renaming={isRenaming}
									>
										{isRenaming ? (
											<input
												className="chat-sidebar__rename"
												value={renaming.draft}
												maxLength={100}
												onChange={event => setRenaming({ id: conversation.id, draft: event.target.value })}
												onKeyDown={handleRenameKeyDown}
												onBlur={commitRename}
												onFocus={event => event.target.select()}
												aria-label="Chat name"
												data-local-escape
												autoFocus
											/>
										) : isConfirming ? (
											<div className="chat-sidebar__confirm">
												<span>Delete this chat?</span>
												<button
													className="chat-sidebar__confirm-yes"
													type="button"
													onClick={() => handleDelete(conversation.id)}
												>
													Delete
												</button>
												<button type="button" onClick={() => setConfirmingDelete(null)}>
													Cancel
												</button>
											</div>
										) : (
											<>
												<button
													className="chat-sidebar__item-link"
													type="button"
													onClick={() => onSelectConversation(conversation.id)}
													aria-current={isActive ? "page" : undefined}
													title={conversation.title}
												>
													{conversation.title}
												</button>
												<button
													className="chat-sidebar__item-action"
													type="button"
													onClick={() => startRename(conversation)}
													aria-label={`Rename chat: ${conversation.title}`}
													title="Rename chat"
												>
													<PencilIcon />
												</button>
												<button
													className="chat-sidebar__item-action chat-sidebar__item-action--danger"
													type="button"
													onClick={() => setConfirmingDelete(conversation.id)}
													aria-label={`Delete chat: ${conversation.title}`}
													title="Delete chat"
												>
													<TrashIcon size={15} />
												</button>
											</>
										)}
									</li>
								);
							})}
						</ul>
					</section>
				))}
			</div>

			<div className="chat-sidebar__user" title={userId}>
				<span className="chat-sidebar__avatar" aria-hidden="true">
					{initialsOf(userName)}
				</span>
				<span className="chat-sidebar__user-text">
					<span className="chat-sidebar__user-name">{userName}</span>
					<span className="chat-sidebar__user-status" data-state={connection}>
						<span className="chat-sidebar__status-dot" />
						{CONNECTION_LABEL[connection]}
					</span>
				</span>
			</div>
		</nav>
	);
}
