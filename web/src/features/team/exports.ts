// Teammates, teams and computers. The only file other code imports from this feature.
export { TeamWho } from './TeamWho';
export { validateTeamSearch, type TeamSearch } from './routing';
export { TeammateChat, AgentChat } from './TeammateChat';
export { AgentPage } from './AgentPage';
export { PersonPage } from './PersonPage';
export { TeamPage } from './TeamPage';
export { ComputerPage, type ComputerPageProps } from './ComputerPage';
export { BrowserDialog, type BrowserDialogProps } from './BrowserView';
export { agentsQuery, groupsQuery, computersQuery, type AgentRow } from './queries';
// The model picker for one agent, and Add agent for pages outside the team pages (Settings' fleet).
export { ModelPicker } from './ModelPicker';
export { AddAgentButton } from './AddTeammate';
export { teamSlug, whoHref } from './model';
