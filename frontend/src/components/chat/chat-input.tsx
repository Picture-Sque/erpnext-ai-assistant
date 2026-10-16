import { FormEvent, KeyboardEvent, RefObject, useEffect } from "react";
import { SendIcon } from "./chat-icons";

interface ChatInputProps {
	value: string;
	onChange: (value: string) => void;
	onSendMessage: (message: string) => boolean | Promise<boolean>;
	textareaRef: RefObject<HTMLTextAreaElement | null>;
	isSending?: boolean;
}

export function ChatInput({ value, onChange, onSendMessage, textareaRef, isSending = false }: ChatInputProps) {
	useEffect(() => {
		const textarea = textareaRef.current;

		if (!textarea) return;

		textarea.style.height = "0px";
		textarea.style.height = `${Math.min(textarea.scrollHeight, 220)}px`;
	}, [textareaRef, value]);

	const canSend = value.trim() !== "" && !isSending;

	const submitMessage = async () => {
		if (!canSend) return;
		const text = value;

		// Clear right away rather than after the reply arrives. A refused send is
		// rejected before any network call, so restoring cannot overwrite new typing.
		onChange("");
		const sent = await onSendMessage(text);

		if (!sent) {
			onChange(text);
		}
	};

	const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		submitMessage();
	};

	const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
		if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;

		event.preventDefault();
		submitMessage();
	};

	return (
		<form className="chat-input" onSubmit={handleSubmit}>
			<div className="chat-input__frame">
				<textarea
					ref={textareaRef}
					className="chat-input__textarea"
					placeholder="Ask anything about your ERP…"
					rows={1}
					value={value}
					onChange={event => onChange(event.target.value)}
					onKeyDown={handleKeyDown}
					aria-label="Message the assistant"
				/>

				<button
					className="chat-input__send"
					disabled={!canSend}
					type="submit"
					aria-label={isSending ? "Waiting for the reply" : "Send message"}
					title={isSending ? "Waiting for the reply" : "Send"}
				>
					<SendIcon size={17} />
				</button>
			</div>

			<p className="chat-input__hint">Enter to send · Shift+Enter for a new line</p>
		</form>
	);
}
