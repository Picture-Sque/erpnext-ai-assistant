import { useCallback, useEffect, useRef, useState } from "react";
import { useDemoChat } from "../../hooks/useDemoChat";
import { useScrollControls } from "../../hooks/useScrollControls";
import { ChatEmptyState } from "./chat-empty-state";
import { ChatHeader } from "./chat-header";
import { ChatInput } from "./chat-input";
import { ChatMessages } from "./chat-messages";
import { ChatScrollButtons } from "./chat-scroll-buttons";
import { ChatSidebar } from "./chat-sidebar";

interface ChatUIProps {
	isActive?: boolean;
	onClose?: () => void;
}

// Below this width the sidebar becomes a drawer over the chat.
const NARROW_QUERY = "(max-width: 860px)";

const isNarrowViewport = () => typeof window !== "undefined" && window.matchMedia(NARROW_QUERY).matches;

export function ChatUI({ isActive = true, onClose }: ChatUIProps) {
	const {
		authUser,
		connection,
		messages,
		conversationId,
		conversations,
		historyStatus,
		isLoadingConversation,
		isSending,
		isSendingHere,
		sendMessage,
		startNewChat,
		loadConversation,
		deleteConversation,
		renameConversation,
		refreshConversations
	} = useDemoChat();

	const [draft, setDraft] = useState("");
	const [isNarrow, setIsNarrow] = useState(isNarrowViewport);
	const [isSidebarOpen, setIsSidebarOpen] = useState(() => !isNarrowViewport());
	const messagesContainerRef = useRef<HTMLDivElement>(null);
	const textareaRef = useRef<HTMLTextAreaElement>(null);

	const { isAtBottom, isOverflowing, handleScroll, scrollToBottom, measure } =
		useScrollControls(messagesContainerRef);

	useEffect(() => {
		const media = window.matchMedia(NARROW_QUERY);
		const update = () => {
			setIsNarrow(media.matches);
			setIsSidebarOpen(!media.matches);
		};
		media.addEventListener("change", update);
		return () => media.removeEventListener("change", update);
	}, []);

	// Jump straight to the end when a whole conversation loads; glide for a single new message.
	const previousCountRef = useRef(0);
	useEffect(() => {
		const added = messages.length - previousCountRef.current;
		previousCountRef.current = messages.length;
		measure();
		scrollToBottom(added > 1 || added < 0 ? "auto" : "smooth");
	}, [messages.length, isSendingHere, conversationId, measure, scrollToBottom]);

	const focusComposer = useCallback(() => {
		window.requestAnimationFrame(() => textareaRef.current?.focus());
	}, []);

	useEffect(() => {
		if (isActive) focusComposer();
	}, [isActive, focusComposer]);

	const closeSidebarIfNarrow = () => {
		if (isNarrow) setIsSidebarOpen(false);
	};

	const handleNewChat = () => {
		startNewChat();
		closeSidebarIfNarrow();
		focusComposer();
	};

	const handleSelectConversation = (id: string) => {
		if (id !== conversationId) loadConversation(id);
		closeSidebarIfNarrow();
		focusComposer();
	};

	const handlePickSuggestion = (prompt: string) => {
		if (prompt.endsWith(" ")) {
			setDraft(prompt);
			focusComposer();
			return;
		}
		sendMessage(prompt);
	};

	const activeTitle = conversations.find(c => c.id === conversationId)?.title ?? "New chat";
	const showEmptyState = messages.length === 0 && !isLoadingConversation && !isSendingHere;

	return (
		<div className="chat-app" data-sidebar-open={isSidebarOpen} data-narrow={isNarrow}>
			<div className="chat-app__sidebar">
				<ChatSidebar
					conversations={conversations}
					activeConversationId={conversationId}
					historyStatus={historyStatus}
					userName={authUser.fullName}
					userId={authUser.username}
					connection={connection}
					onNewChat={handleNewChat}
					onSelectConversation={handleSelectConversation}
					onDeleteConversation={deleteConversation}
					onRenameConversation={renameConversation}
					onRetryHistory={refreshConversations}
					onCollapse={() => setIsSidebarOpen(false)}
				/>
			</div>

			{isNarrow && isSidebarOpen ? (
				<div className="chat-app__scrim" onClick={() => setIsSidebarOpen(false)} aria-hidden="true" />
			) : null}

			<main className="chat-main" aria-label="AI assistant chat">
				<ChatHeader
					title={activeTitle}
					isSidebarOpen={isSidebarOpen}
					onOpenSidebar={() => setIsSidebarOpen(true)}
					onNewChat={handleNewChat}
					onClose={onClose}
				/>

				<div className="chat-main__body">
					<div ref={messagesContainerRef} className="chat-stream" onScroll={handleScroll}>
						{isLoadingConversation ? (
							<p className="chat-stream__note">Loading conversation…</p>
						) : null}

						{showEmptyState ? (
							<ChatEmptyState userName={authUser.fullName} onPickSuggestion={handlePickSuggestion} />
						) : (
							<ChatMessages messages={messages} isThinking={isSendingHere} />
						)}
					</div>

					<div className="chat-main__composer">
						<ChatScrollButtons
							isAtBottom={isAtBottom}
							isOverflowing={isOverflowing}
							scrollToBottom={scrollToBottom}
						/>
						<ChatInput
							value={draft}
							onChange={setDraft}
							onSendMessage={sendMessage}
							textareaRef={textareaRef}
							isSending={isSending}
						/>
					</div>
				</div>
			</main>
		</div>
	);
}
