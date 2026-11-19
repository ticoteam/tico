/* Add computer's commands, as the old app writes them (ui/app/settings-devices.ts dockerRunnerCommands and
   windowsRunnerCommands): one line puts the runner on the computer with the one-time code, keeping itself on this
   server's release. The fields come from GET /api/v2/config, which the generated type does not list yet. */

export interface RunnerConfig {
  runner_url?: string;
  compose_project?: string;
  server_network?: string;
  /** A quick start: the server answers on this computer only. */
  local?: boolean;
  runner_compat?: { version?: string };
}

export type ComputerKind = 'server' | 'mac' | 'windows';
export interface Command {
  note: string;
  line: string;
}

const RELEASES = 'https://github.com/ticoteam/tico/releases';
const shellSafe = (v: string) => v.replace(/["$`\\\n]/g, '');

/** The release the server runs, as its installer's tag; '' for a build with none. */
export function releaseTag(c: RunnerConfig): string {
  const v = String(c.runner_compat?.version ?? '').replace(/^v/, '');
  return /^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$/.test(v) ? `v${v}` : '';
}

/** The runner's name follows from the code, so one Add computer's commands agree. Starts with a letter or digit. */
export function runnerName(c: RunnerConfig, code: string): string {
  const project =
    String(c.compose_project || 'tico')
      .toLowerCase()
      .replace(/[^a-z0-9-]/g, '-')
      .replace(/^-+/, '')
      .slice(0, 20) || 'tico';
  return `${project}-${code.toLowerCase().replace(/[^a-z0-9]/g, '').slice(0, 8) || 'computer'}`;
}

/** The main line first; the rest under "Other ways". */
export function installCommands(c: RunnerConfig, origin: string, kind: ComputerKind, code: string, label: string): Command[] {
  const tag = releaseTag(c);
  const url = (c.runner_url || origin).replace(/\/+$/, '');
  const name = runnerName(c, code);
  const asset = (file: string) => (tag ? `${RELEASES}/download/${tag}/${file}` : `${RELEASES}/latest/download/${file}`);
  if (kind === 'windows') {
    const quoted = `'${shellSafe(label).replace(/'/g, "''")}'`;
    return [
      {
        note: 'In PowerShell as administrator. Sets up WSL 2 and Ubuntu if needed, and may ask for a restart',
        line: `& ([scriptblock]::Create((irm ${asset('install-wsl.ps1')}))) -Url ${url} -Code '${code}' -Label ${quoted} -Name ${name}`,
      },
    ];
  }
  // A quick-start server is not reachable at 127.0.0.1 from inside the runner's container: it joins the network.
  const local = !!c.local;
  const join = local ? '--url http://server:8765' : `--url ${url}`;
  const network = String(c.server_network || 'tico_default').replace(/[^a-zA-Z0-9_.-]/g, '');
  const container = `tico-runner-${name}`;
  const quoted = `"${shellSafe(label)}"`;
  return [
    {
      note: local
        ? 'On this computer. Installs Docker if it is missing'
        : kind === 'mac'
          ? 'On that Mac, with Docker Desktop running'
          : 'On the server. Installs Docker if it is missing',
      line: `curl -fsSL ${asset('install.sh')} -o install.sh && sh install.sh --runner --name ${name} ${join}${local ? ` --server-network ${network}` : ''} --code '${code}' --label ${quoted}`,
    },
    {
      note: "With plain Docker. It will not follow the server's releases",
      line: `docker run -d --name ${container} --restart unless-stopped${local ? ` --network ${network}` : ''} -v ${container}_runner-home:/home/runner ghcr.io/ticoteam/tico-runner:${tag || 'latest'} join ${join} --code '${code}' --label ${quoted}`,
    },
  ];
}
