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

export function CloseIcon({ size = 16, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<path d="M6.75 6.75 17.25 17.25M17.25 6.75 6.75 17.25" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
		</svg>
	);
}

export function MaximizeIcon({ size = 16, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
		</svg>
	);
}

export function MinimizeIcon({ size = 16, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<path d="M4 14h6v6M20 10h-6V4M14 10l7-7M10 14l-7 7" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
		</svg>
	);
}

export function PlusIcon({ size = 16, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<path d="M12 5v14M5 12h14" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
		</svg>
	);
}
export function SidebarIcon({ size = 18, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<rect height="16" rx="3" stroke="currentColor" strokeWidth="1.8" width="18" x="3" y="4" />
			<path d="M9.5 4v16" stroke="currentColor" strokeWidth="1.8" />
		</svg>
	);
}

export function NewChatIcon({ size = 18, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<path d="M11 4.5H7.5a3 3 0 0 0-3 3v9a3 3 0 0 0 3 3h9a3 3 0 0 0 3-3V13" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
			<path d="M17.6 3.9a1.9 1.9 0 0 1 2.7 2.7l-7.1 7.1-3.4.7.7-3.4 7.1-7.1Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.8" />
		</svg>
	);
}

export function SearchIcon({ size = 16, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<circle cx="11" cy="11" r="6.5" stroke="currentColor" strokeWidth="1.8" />
			<path d="m16 16 4 4" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
		</svg>
	);
}

export function TrashIcon({ size = 16, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<path d="M4.5 7h15M9.5 7V5.2c0-.4.3-.7.7-.7h3.6c.4 0 .7.3.7.7V7M6.5 7l.8 11.3c.1 1 .9 1.7 1.9 1.7h5.6c1 0 1.8-.7 1.9-1.7L17.5 7" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.7" />
		</svg>
	);
}

export function CopyIcon({ size = 15, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<rect height="12" rx="2.5" stroke="currentColor" strokeWidth="1.7" width="12" x="8.5" y="8.5" />
			<path d="M15.5 5.5v-.3A1.7 1.7 0 0 0 13.8 3.5H5.2a1.7 1.7 0 0 0-1.7 1.7v8.6a1.7 1.7 0 0 0 1.7 1.7h.3" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" />
		</svg>
	);
}

export function CheckIcon({ size = 15, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<path d="m5 12.5 4.5 4.5L19 7.5" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.9" />
		</svg>
	);
}

export function PencilIcon({ size = 15, className }: IconProps) {
	return (
		<svg aria-hidden="true" className={className} fill="none" height={size} viewBox="0 0 24 24" width={size}>
			<path d="M15.2 5.2a2.1 2.1 0 0 1 3 3L8.5 17.9l-4 1 1-4 9.7-9.7Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.7" />
			<path d="m13.5 7 3.5 3.5" stroke="currentColor" strokeWidth="1.7" />
		</svg>
	);
}
