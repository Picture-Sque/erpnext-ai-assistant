import { useEffect, useRef } from "react";
import { useDemoChat } from "../../hooks/useDemoChat";
import { useScrollControls } from "../../hooks/useScrollControls";
import { ChatInput } from "./chat-input";
import { ChatHeader } from "./chat-header";
import { ChatMessages } from "./chat-messages";
import { ChatScrollButtons } from "./chat-scroll-buttons";

export function ChatUI() {
	const { messages, sendMessage } = useDemoChat();
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
		<main className="app-shell">
			<section className="chat-page" aria-label="AI assistant chat interface">
				<div className="chat-panel">
					<ChatHeader />

					<div className="chat-panel__body">
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

						<ChatInput onSendMessage={sendMessage} />
					</div>
				</div>
			</section>
		</main>
	);
}