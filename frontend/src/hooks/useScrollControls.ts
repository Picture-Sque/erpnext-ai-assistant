import { RefObject, useCallback, useEffect, useRef, useState } from "react";

const BOTTOM_TOLERANCE_PX = 4;

export const useScrollControls = (containerRef: RefObject<HTMLDivElement | null>) => {
	const [isAtTop, setIsAtTop] = useState(true);
	const [isAtBottom, setIsAtBottom] = useState(true);
	const [isOverflowing, setIsOverflowing] = useState(false);
	// Whether the view should follow the end of the content when it grows.
	// Cleared only when the user scrolls away from the bottom.
	const stickToBottomRef = useRef(true);

	const distanceFromBottom = (container: HTMLDivElement) =>
		container.scrollHeight - container.clientHeight - container.scrollTop;

	const measure = useCallback(() => {
		const container = containerRef.current;

		if (!container) return;

		setIsOverflowing(container.scrollHeight > container.clientHeight + 1);
		setIsAtTop(container.scrollTop <= BOTTOM_TOLERANCE_PX);
		setIsAtBottom(distanceFromBottom(container) <= BOTTOM_TOLERANCE_PX);
	}, [containerRef]);

	const handleScroll = useCallback(() => {
		const container = containerRef.current;

		if (container) stickToBottomRef.current = distanceFromBottom(container) <= BOTTOM_TOLERANCE_PX;
		measure();
	}, [containerRef, measure]);

	const scrollToTop = useCallback(() => {
		stickToBottomRef.current = false;
		containerRef.current?.scrollTo({ top: 0, behavior: "smooth" });
	}, [containerRef]);

	const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
		const container = containerRef.current;

		if (!container) return;

		stickToBottomRef.current = true;
		container.scrollTo({ top: container.scrollHeight, behavior });
		// Re-measure once a smooth scroll has settled; not every browser fires a final scroll event.
		window.setTimeout(() => {
			stickToBottomRef.current = true;
			measure();
		}, behavior === "smooth" ? 450 : 0);
	}, [containerRef, measure]);

	useEffect(() => {
		measure();

		const container = containerRef.current;

		if (!container || typeof ResizeObserver === "undefined") return;

		// Content can change height after it is scrolled into place (markdown tables,
		// loading notes, fonts). Follow it while the user is at the bottom.
		const resizeObserver = new ResizeObserver(() => {
			if (stickToBottomRef.current) container.scrollTop = container.scrollHeight;
			measure();
		});

		const observeChildren = () => {
			resizeObserver.disconnect();
			resizeObserver.observe(container);
			Array.from(container.children).forEach(child => resizeObserver.observe(child));
		};

		observeChildren();
		const mutationObserver = new MutationObserver(observeChildren);
		mutationObserver.observe(container, { childList: true });

		return () => {
			mutationObserver.disconnect();
			resizeObserver.disconnect();
		};
	}, [containerRef, measure]);

	return {
		isAtTop,
		isAtBottom,
		isOverflowing,
		handleScroll,
		scrollToTop,
		scrollToBottom,
		measure
	};
};
