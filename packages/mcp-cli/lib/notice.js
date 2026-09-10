/** The agent-write disclosure that connect and install print on success. */

/** One shared constant so the connect and install wording can never drift apart. */
export const AGENT_WRITE_NOTICE = [
  "Heads-up: agents connected through this MCP server can write to your organization's",
  "knowledge base - as they work they may capture decisions and answers into shared",
  "collections. Review captures anytime: Dashboard -> Documents -> Written by agents.",
];

/** Print the notice through the caller's logger, one log() call per line. */
export function printAgentWriteNotice(log) {
  for (const line of AGENT_WRITE_NOTICE) {
    log(line);
  }
}
