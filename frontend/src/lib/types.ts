export type RouteType = "conversational" | "retrieval" | "blocked";

export type LinkType = "anchor" | "text_fragment" | "document";

export interface Source {
  citation: number;
  url: string;
  document_url: string;
  link_type: LinkType;
  title: string;
  ticker?: string | null;
  cik?: string | null;
  form_type?: string | null;
  accession_number?: string | null;
  document_id?: string | null;
  year?: number | null;
  part?: string | null;
  item_id?: string | null;
  item_title?: string | null;
  section: string[];
  anchor_id?: string | null;
  source_dom_id?: string | null;
  point_id?: string | null;
  snippet?: string | null;
  relevance_score?: number | null;
}

export interface QueryResponse {
  type: RouteType;
  answer: string | null;
  sources: Source[];
}

export interface IngestResponse {
  ticker: string;
  cik: string;
  company: string;
  year: number | null;
  form_type: string;
  cached: boolean;
  status: "queued" | "cached";
}

export type MessageStatus = "pending" | "complete" | "error" | "blocked";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: MessageStatus;
  route?: RouteType;
  sources?: Source[];
  error?: string;
  createdAt: number;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
}

export interface ChatStore {
  conversations: Conversation[];
  activeId: string | null;
}

export interface AuthUser {
  id: number;
  username: string;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: AuthUser;
}
