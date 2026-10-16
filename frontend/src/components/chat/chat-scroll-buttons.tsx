import { ArrowDownIcon } from "./chat-icons";

interface ChatScrollButtonsProps {
	isAtBottom: boolean;
	isOverflowing: boolean;
	scrollToBottom: () => void;
}

export function ChatScrollButtons({ isAtBottom, isOverflowing, scrollToBottom }: ChatScrollButtonsProps) {
	if (!isOverflowing || isAtBottom) return null;

	return (
		<button
			className="chat-scroll-button"
			type="button"
			onClick={() => scrollToBottom()}
			aria-label="Scroll to latest message"
			title="Scroll to latest"
		>
			<ArrowDownIcon />
		</button>
	);
}
