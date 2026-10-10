import { useEffect, useRef } from "react";
import { useDemoChat } from "../../hooks/useDemoChat";
import { useScrollControls } from "../../hooks/useScrollControls";
import { ChatInput } from "./chat-input";
import { ChatHeader } from "./chat-header";
import { ChatMessages } from "./chat-messages";
import { ChatScrollButtons } from "./chat-scroll-buttons";

interface ChatUIProps {
	isActive?: boolean;
	onClose?: () => void;
	isExpanded?: boolean;
	onToggleExpand?: () => void;
}

export function ChatUI({
	isActive = true,
	onClose,
	isExpanded = false,
	onToggleExpand
}: ChatUIProps) {
	const { messages, sendMessage, currentUser, startNewChat } = useDemoChat();
	const messagesContainerRef = useRef<HTMLDivElement>(null);

	const {
		isAtTop,
		isAtBottom,
		isOverflowing,
		handleScroll,
		scrollToTop,
		scrollToBottom,
		measure
	} = useScrollControls(messagesContainerRef);

	useEffect(() => {
		measure();
		scrollToBottom();
	}, [messages.length, measure, scrollToBottom]);

	return (
		<div className="app-shell" data-expanded={isExpanded}>
			<section
				className={`chat-page ${isExpanded ? "chat-page--expanded" : ""}`}
				data-expanded={isExpanded}
				aria-label="AI assistant chat interface"
			>
				<div className="chat-panel" data-expanded={isExpanded}>
					<ChatHeader
						onClose={onClose}
						currentUser={currentUser}
						onNewChat={startNewChat}
						isExpanded={isExpanded}
						onToggleExpand={onToggleExpand}
					/>

					<div className="chat-panel__body">
						<div className="chat-panel__spacer" aria-hidden="true" />
						<div className="chat-stream-wrap">
							<div
								ref={messagesContainerRef}
								className="chat-stream"
								onScroll={handleScroll}
							>
								<ChatMessages messages={messages} />
							</div>

							<ChatScrollButtons
								isAtTop={isAtTop}
								isAtBottom={isAtBottom}
								isOverflowing={isOverflowing}
								scrollToTop={scrollToTop}
								scrollToBottom={scrollToBottom}
							/>
						</div>

						<ChatInput autoFocus={isActive} onSendMessage={sendMessage} />
					</div>
				</div>
			</section>
		</div>
	);
}