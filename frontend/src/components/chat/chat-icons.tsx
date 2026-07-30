interface IconProps {
	size?: number;
	className?: string;
}

export function ChatLogoIcon({ size = 18, className }: IconProps) {
	return (
		<svg
			aria-hidden="true"
			className={className}
			fill="none"
			height={size}
			viewBox="0 0 24 24"
			width={size}
		>
			<path
				d="M12 3.75c4.556 0 8.25 3.26 8.25 7.28 0 4.019-3.694 7.28-8.25 7.28H9.14l-3.99 2.49v-3.3C3.88 15.95 3.75 14.55 3.75 11.03 3.75 7.01 7.444 3.75 12 3.75Z"
				stroke="currentColor"
				strokeLinecap="round"
				strokeLinejoin="round"
				strokeWidth="1.75"
			/>
			<path d="M8.1 11.02h.01M12 11.02h.01M15.9 11.02h.01" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.2" />
		</svg>
	);
}

export function SendIcon({ size = 18, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<path d="M5 12 19 5l-3.5 14-4.3-6L5 12Z" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
			<path d="m11.2 13.9 7.8-8.9" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
		</svg>
	);
}

export function ArrowUpIcon({ size = 18, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<path d="m7 14 5-5 5 5" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
		</svg>
	);
}

export function ArrowDownIcon({ size = 18, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<path d="m7 10 5 5 5-5" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
		</svg>
	);
}

export function UserCircleIcon({ size = 28, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<path d="M12 13.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Z" fill="currentColor" />
			<path d="M4.5 19.25a7.6 7.6 0 0 1 15 0" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
		</svg>
	);
}