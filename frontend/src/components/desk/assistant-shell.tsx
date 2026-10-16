import { useCallback, useEffect, useRef, useState } from "react";
import { ChatLogoIcon } from "../chat/chat-icons";
import { ChatUI } from "../chat/chat-ui";

const FOCUSABLE = "textarea, input, button, [href], select, [tabindex]:not([tabindex='-1'])";

export function DeskAssistantShell() {
	const [isOpen, setIsOpen] = useState(false);
	const toggleButtonRef = useRef<HTMLButtonElement>(null);
	const windowRef = useRef<HTMLDivElement>(null);
	const wasOpenRef = useRef(false);

	const closeAssistant = useCallback(() => {
		setIsOpen(false);
	}, []);

	const openAssistant = useCallback(() => {
		setIsOpen(true);
	}, []);

	// Return focus to the launcher when the window closes.
	useEffect(() => {
		if (!isOpen && wasOpenRef.current) toggleButtonRef.current?.focus();
		wasOpenRef.current = isOpen;
	}, [isOpen]);

	// The window covers the whole Desk, so the page behind it should not scroll.
	useEffect(() => {
		if (!isOpen) return;

		const previousOverflow = document.body.style.overflow;
		document.body.style.overflow = "hidden";

		return () => {
			document.body.style.overflow = previousOverflow;
		};
	}, [isOpen]);

	useEffect(() => {
		const handleKeyDown = (event: KeyboardEvent) => {
			if (!isOpen) return;

			if (event.key === "Escape") {
				// Fields such as the chat rename box use Escape to cancel their own edit.
				if ((event.target as HTMLElement | null)?.closest?.("[data-local-escape]")) return;
				event.preventDefault();
				event.stopPropagation();
				closeAssistant();
				return;
			}

			if (event.key !== "Tab") return;

			const focusable = Array.from(
				windowRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []
			).filter(element => !element.hasAttribute("disabled") && element.offsetParent !== null);

			if (focusable.length === 0) return;

			const activeElement = document.activeElement as HTMLElement | null;
			const currentIndex = activeElement ? focusable.indexOf(activeElement) : -1;
			const nextIndex = event.shiftKey
				? (currentIndex - 1 + focusable.length) % focusable.length
				: (currentIndex + 1) % focusable.length;

			event.preventDefault();
			focusable[nextIndex]?.focus();
		};

		document.addEventListener("keydown", handleKeyDown, true);

		return () => document.removeEventListener("keydown", handleKeyDown, true);
	}, [closeAssistant, isOpen]);

	return (
		<div className="desk-assistant" data-open={isOpen}>
			<div
				ref={windowRef}
				className="desk-assistant__window"
				data-open={isOpen}
				id="ai-assistant-window"
				role="dialog"
				aria-modal="true"
				aria-label="AI assistant"
				aria-hidden={!isOpen}
				inert={!isOpen}
			>
				<ChatUI isActive={isOpen} onClose={closeAssistant} />
			</div>

			<button
				ref={toggleButtonRef}
				className="desk-assistant__toggle"
				type="button"
				onClick={openAssistant}
				aria-expanded={isOpen}
				aria-controls="ai-assistant-window"
				aria-label="Open AI assistant"
				hidden={isOpen}
			>
				<ChatLogoIcon className="desk-assistant__toggle-icon" size={18} />
				<span className="desk-assistant__toggle-text">AI</span>
			</button>
		</div>
	);
}
