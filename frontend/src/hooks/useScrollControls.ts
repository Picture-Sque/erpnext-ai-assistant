import { RefObject, useCallback, useEffect, useState } from "react";

export const useScrollControls = (containerRef: RefObject<HTMLDivElement | null>) => {
	const [isAtTop, setIsAtTop] = useState(true);
	const [isAtBottom, setIsAtBottom] = useState(true);
	const [isOverflowing, setIsOverflowing] = useState(false);

	const measure = useCallback(() => {
		const container = containerRef.current;

		if (!container) return;

		const overflowing = container.scrollHeight > container.clientHeight + 1;
		const distanceFromBottom =
			container.scrollHeight - container.clientHeight - container.scrollTop;

		setIsOverflowing(overflowing);
		setIsAtTop(container.scrollTop <= 4);
		setIsAtBottom(distanceFromBottom <= 4);
	}, [containerRef]);

	const handleScroll = useCallback(() => {
		measure();
	}, [measure]);

	const scrollToTop = useCallback(() => {
		containerRef.current?.scrollTo({ top: 0, behavior: "smooth" });
	}, [containerRef]);

	const scrollToBottom = useCallback(() => {
		const container = containerRef.current;

		if (!container) return;

		container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
	}, [containerRef]);

	useEffect(() => {
		measure();

		const container = containerRef.current;

		if (!container || typeof ResizeObserver === "undefined") return;

		const resizeObserver = new ResizeObserver(() => {
			measure();
		});

		resizeObserver.observe(container);

		return () => {
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