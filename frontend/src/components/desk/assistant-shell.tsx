import { useCallback, useEffect, useRef, useState } from "react";
import { ChatLogoIcon } from "../chat/chat-icons";
import { ChatUI } from "../chat/chat-ui";

export function DeskAssistantShell() {
	const [isOpen, setIsOpen] = useState(false);
	const toggleButtonRef = useRef<HTMLButtonElement>(null);
	const sidebarRef = useRef<HTMLDivElement>(null);

	const closeAssistant = useCallback(() => {
		setIsOpen(false);
	}, []);

	const toggleAssistant = useCallback(() => {
		setIsOpen(previousState => !previousState);
	}, []);

	useEffect(() => {
		if (!isOpen) {
			toggleButtonRef.current?.focus();
			return;
		}

		const frame = window.requestAnimationFrame(() => {
			const firstFocusable = sidebarRef.current?.querySelector<HTMLElement>(
				"textarea, button, [href], input, select, [tabindex]:not([tabindex='-1'])"
			);

			firstFocusable?.focus();
		});

		return () => window.cancelAnimationFrame(frame);
	}, [isOpen]);

	useEffect(() => {
		const handleKeyDown = (event: KeyboardEvent) => {
			if (!isOpen) return;

			if (event.key === "Escape") {
				event.preventDefault();
				closeAssistant();
				return;
			}

			if (event.key !== "Tab") return;

			const focusableElements = sidebarRef.current?.querySelectorAll<HTMLElement>(
				"textarea, button, [href], input, select, [tabindex]:not([tabindex='-1'])"
			);

			if (!focusableElements || focusableElements.length === 0) return;

			const focusable = Array.from(focusableElements).filter(element => !element.hasAttribute("disabled"));
			if (focusable.length === 0) return;

			const activeElement = document.activeElement as HTMLElement | null;
			const currentIndex = focusable.indexOf(activeElement ?? focusable[0]);
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
				className="desk-assistant__overlay"
				data-open={isOpen}
				onClick={closeAssistant}
				aria-hidden={!isOpen}
			/>

			<aside
				ref={sidebarRef}
				className="desk-assistant__sidebar"
				data-open={isOpen}
				id="ai-assistant-sidebar"
				role="dialog"
				aria-modal="true"
				aria-label="AI assistant sidebar"
			>
				<ChatUI isActive={isOpen} onClose={closeAssistant} />
			</aside>

			<button
				ref={toggleButtonRef}
				className="desk-assistant__toggle"
				type="button"
				onClick={toggleAssistant}
				aria-expanded={isOpen}
				aria-controls="ai-assistant-sidebar"
				aria-label={isOpen ? "Close AI assistant" : "Open AI assistant"}
			>
				<ChatLogoIcon className="desk-assistant__toggle-icon" size={18} />
				<span className="desk-assistant__toggle-text">AI</span>
			</button>
		</div>
	);
}