import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { SendIcon } from "./chat-icons";

interface ChatInputProps {
	onSendMessage: (message: string) => boolean;
	autoFocus?: boolean;
}

export function ChatInput({ onSendMessage, autoFocus = false }: ChatInputProps) {
	const [draft, setDraft] = useState("");
	const textareaRef = useRef<HTMLTextAreaElement>(null);

	useEffect(() => {
		const textarea = textareaRef.current;

		if (!textarea) return;

		textarea.style.height = "0px";
		textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
	}, [draft]);

	useEffect(() => {
		if (!autoFocus) return;

		const timeoutId = window.setTimeout(() => {
			textareaRef.current?.focus();
		}, 0);

		return () => window.clearTimeout(timeoutId);
	}, [autoFocus]);

	const submitMessage = () => {
		const sent = onSendMessage(draft);

		if (sent) {
			setDraft("");
		}
	};

	const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		submitMessage();
	};

	const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
		if (event.key !== "Enter" || event.shiftKey) return;

		event.preventDefault();
		submitMessage();
	};

	return (
		<form className="chat-input" onSubmit={handleSubmit}>
			<div className="chat-input__frame">
				<textarea
					ref={textareaRef}
					className="chat-input__textarea"
					placeholder="Ask anything about your ERP..."
					value={draft}
					onChange={event => setDraft(event.target.value)}
					onKeyDown={handleKeyDown}
				/>

				<button
					className="chat-input__send"
					disabled={!draft.trim()}
					type="submit"
					aria-label="Send message"
				>
					<SendIcon />
				</button>
			</div>

			<p className="chat-input__hint">Press Enter to send • Shift+Enter for a newline</p>
		</form>
	);
}