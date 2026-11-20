// Teammates, teams and computers. The only file other code imports from this feature.
export { TeamWho } from './TeamWho';
export { validateTeamSearch, type TeamSearch } from './routing';
export { TeammateChat, AgentChat } from './TeammateChat';
export { AgentPage } from './AgentPage';
export { PersonPage } from './PersonPage';
export { ComputerPage, type ComputerPageProps } from './ComputerPage';
export { BrowserDialog, type BrowserDialogProps } from './BrowserView';
export { agentsQuery, groupsQuery, computersQuery, type AgentRow } from './queries';
// The model picker for one agent, and Add agent for Settings' fleet.
export { ModelPicker } from './ModelPicker';
export { AddAgentButton, AddTeammate } from './AddTeammate';
// A team's Access, from the menu's ⋯ on the team (the root holds it open).
export { TeamAccess } from './dialogs';
export { teamSlug, whoHref } from './model';
