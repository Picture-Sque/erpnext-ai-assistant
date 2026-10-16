import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChatMessage } from "../../types/chat";
import { ChatLogoIcon, CheckIcon, CopyIcon } from "./chat-icons";

interface ChatMessageProps {
	message: ChatMessage;
}

export function ChatMessageView({ message }: ChatMessageProps) {
	if (message.role === "user") {
		return (
			<article className="chat-message chat-message--user">
				<div className="chat-message__bubble" title={message.timestamp}>
					{message.content}
				</div>
			</article>
		);
	}

	return (
		<article className="chat-message chat-message--assistant" data-error={message.isError ? "true" : undefined}>
			<span className="chat-message__avatar" aria-hidden="true">
				<ChatLogoIcon size={16} />
			</span>

			<div className="chat-message__body">
				<div className="chat-markdown">
					<ReactMarkdown
						remarkPlugins={[remarkGfm]}
						components={{
							table: ({ children }) => (
								<div className="chat-markdown__table-wrapper">
									<table className="chat-markdown__table">{children}</table>
								</div>
							),
							pre: ({ children }) => (
								<pre className="chat-markdown__code-block">{children}</pre>
							),
							code: ({ className, children, ...props }: any) => (
								<code
									className={className ? `chat-markdown__block-code ${className}` : "chat-markdown__inline-code"}
									{...props}
								>
									{children}
								</code>
							),
							a: ({ href, children }) => (
								<a
									href={href}
									target="_blank"
									rel="noopener noreferrer"
									className="chat-markdown__link"
								>
									{children}
								</a>
							)
						}}
					>
						{message.content}
					</ReactMarkdown>
				</div>

				<div className="chat-message__actions">
					<CopyButton text={message.content} />
					<span className="chat-message__timestamp">{message.timestamp}</span>
				</div>
			</div>
		</article>
	);
}

function CopyButton({ text }: { text: string }) {
	const [copied, setCopied] = useState(false);

	const copy = async () => {
		try {
			await navigator.clipboard.writeText(text);
			setCopied(true);
			window.setTimeout(() => setCopied(false), 1500);
		} catch (error) {
			console.warn("[Chat Widget] Clipboard write failed:", error);
		}
	};

	return (
		<button
			className="chat-icon-btn chat-icon-btn--small"
			type="button"
			onClick={copy}
			aria-label={copied ? "Copied" : "Copy response"}
			title={copied ? "Copied" : "Copy"}
		>
			{copied ? <CheckIcon /> : <CopyIcon />}
		</button>
	);
}

export function ChatTypingIndicator() {
	return (
		<article className="chat-message chat-message--assistant" aria-live="polite">
			<span className="chat-message__avatar" aria-hidden="true">
				<ChatLogoIcon size={16} />
			</span>
			<div className="chat-message__body">
				<div className="chat-typing" aria-label="Assistant is thinking">
					<span />
					<span />
					<span />
				</div>
			</div>
		</article>
	);
}
