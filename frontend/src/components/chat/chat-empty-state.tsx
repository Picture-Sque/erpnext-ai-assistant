import { ChatLogoIcon } from "./chat-icons";

interface ChatEmptyStateProps {
	userName: string;
	onPickSuggestion: (prompt: string) => void;
}

// Each prompt maps to an existing skill. Prompts ending in a space need a value
// (item, supplier, customer) and are placed in the composer for the user to finish.
const SUGGESTIONS = [
	{ title: "Check stock", detail: "for an item in a warehouse", prompt: "Check stock for " },
	{ title: "Low stock", detail: "items that are running low", prompt: "Show me items running low" },
	{ title: "Find a purchase order", detail: "by PO number or supplier", prompt: "Show me purchase order " },
	{ title: "Create a purchase order", detail: "draft a PO for a supplier", prompt: "Create a purchase order for " },
	{ title: "Best-selling items", detail: "sales analytics by revenue or units", prompt: "What's our best-selling item?" },
	{ title: "Customer order history", detail: "past orders for a customer", prompt: "Show me all orders from " }
];

const greeting = () => {
	const hour = new Date().getHours();
	if (hour < 12) return "Good morning";
	if (hour < 17) return "Good afternoon";
	return "Good evening";
};

export function ChatEmptyState({ userName, onPickSuggestion }: ChatEmptyStateProps) {
	const firstName = userName && userName !== "Guest" ? userName.split(" ")[0] : "";

	return (
		<div className="chat-empty">
			<span className="chat-empty__mark" aria-hidden="true">
				<ChatLogoIcon size={26} />
			</span>
			<h2 className="chat-empty__title">
				{greeting()}
				{firstName ? `, ${firstName}` : ""}
			</h2>
			<p className="chat-empty__subtitle">Ask about stock, orders, suppliers and customers in ERPNext.</p>

			<div className="chat-empty__grid">
				{SUGGESTIONS.map(suggestion => (
					<button
						key={suggestion.title}
						className="chat-empty__card"
						type="button"
						onClick={() => onPickSuggestion(suggestion.prompt)}
					>
						<span className="chat-empty__card-title">{suggestion.title}</span>
						<span className="chat-empty__card-detail">{suggestion.detail}</span>
					</button>
				))}
			</div>
		</div>
	);
}
