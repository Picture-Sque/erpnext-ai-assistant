import { ArrowDownIcon, ArrowUpIcon } from "./chat-icons";

interface ChatScrollButtonsProps {
	isAtTop: boolean;
	isAtBottom: boolean;
	isOverflowing: boolean;
	scrollToTop: () => void;
	scrollToBottom: () => void;
}

export function ChatScrollButtons({
	isAtTop,
	isAtBottom,
	isOverflowing,
	scrollToTop,
	scrollToBottom
}: ChatScrollButtonsProps) {
	if (!isOverflowing) return null;

	return (
		<div className="chat-scroll-buttons" aria-label="Message navigation">
			<button
				className="chat-scroll-button"
				disabled={isAtTop}
				type="button"
				onClick={scrollToTop}
				aria-label="Scroll to top"
			>
				<ArrowUpIcon />
			</button>

			<button
				className="chat-scroll-button"
				disabled={isAtBottom}
				type="button"
				onClick={scrollToBottom}
				aria-label="Scroll to bottom"
			>
				<ArrowDownIcon />
			</button>
		</div>
	);
}