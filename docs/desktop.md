# Tico desktop app

Download **Tico** from the [latest GitHub release](https://github.com/ticoteam/tico/releases/latest).
The same app connects to any Tico server and holds several companies, each with its own sign-in.
A company with its own app offers it in its Tico menu ([below](#your-companys-app)).

## Install

| Computer | File | How |
| --- | --- | --- |
| Mac with an Apple chip (M1 or later), macOS 14 or later | `Tico-<version>-arm64.dmg` | Open it and drag Tico to Applications. |
| Windows, x64 or Arm | `Tico-<version>-setup.exe` | Run it. It installs for your Windows account and opens Tico. |
| Linux, x86-64 | `Tico-<version>-x86_64.AppImage` or `Tico-<version>-amd64.deb` | Make the AppImage executable (`chmod +x`) and open it, or install the package (`sudo apt install ./Tico-<version>-amd64.deb`). |

There is no build for Intel Macs.

The Mac app is signed but not notarized, so macOS may refuse to open it the first time. Click **Done**, open
System Settings > Privacy & Security, click **Open Anyway** beside the message about Tico, and confirm. Later
launches open normally.

The Windows installer is not signed, so Microsoft Defender SmartScreen may show **Windows protected your PC**. Click
**More info**, then **Run anyway**.

## First launch

If this computer has the [older desktop app](#the-older-desktop-app), the new app starts with its list of companies.
Open each one and sign in once: sign-ins do not carry over.

Otherwise the first launch shows **Add company**. Enter your company's server address, for example
`acme.tico.team` or `https://tico.example.com`, and click **Add**; Tico opens it, and you sign in as you would in a
browser. HTTPS is required; `http://localhost` and `http://127.0.0.1` are allowed for a server on your computer. On
Tico Cloud, click **Sign in to Tico** instead: sign in in the window that opens, then tick the companies you want and
add them.

To add another company later, choose **Company > Add company…** in the app's menu.

## Updates

The app checks the latest GitHub release for a new version when it starts, every six hours, and from
**Check for updates…** in the app's menu (the **Tico** menu on a Mac, **File** on Windows and Linux). It downloads a
new version in the background and installs it when you quit, or at once with **Restart to update** in Settings >
General > Tico (not during a call). The pages themselves come from your server and change when it
updates.

## The older desktop app

Before the current app, Tico's desktop app was a different program (`app/` in the repository, files named
`Tico_<version>_...` on each release, such as `Tico_<version>_universal.dmg`). Releases still carry it until installed
copies have moved over. Inside it, the menu shows **A new Tico desktop app is ready.** with a **Download** link for
your computer. Install the new app as above:

- On a Mac, both apps are called Tico, so dragging the new one to Applications replaces the older one (Finder asks
  first).
- On Windows and Linux the two install separately. Remove the older one when you no longer use it.

The new app keeps no sign-in from the older one: sign in to each company again. The rest of this section, and the
sections after it, describe the older app.

The app can keep [signed visuals](visuals.md) inline when the selected server uses
a separate HTTPS address for them. After sign-in, the server supplies that address
and the app accepts only its signed visual paths. Changing servers, signing out or
starting another sign-in clears the allowance. Visual pages keep their sandbox;
their origin is not granted native command or app-log permissions. If this app build cannot confirm
the visual address, the page stays usable and those visuals appear as file links.

Links to other sites, and any link that would open a new tab (links in chat and Markdown), open in your
default browser, and email links in your mail app. A sign-in stays in the app window from start to finish,
whichever identity provider it goes through (Cloudflare Access with Google, Microsoft, Okta, GitHub or a one-time PIN,
or the server's own sign-in), and so does any link you follow on its pages until you are back in Tico; **Reload**
(Cmd+R) takes the main window back, and closing a file or meeting window ends its sign-in. Your team's own files and run logs open in a separate app window, so they keep the app's sign-in.

Tico is one ordinary window with the Mac's own title bar: drag it by its title on any page and
resize it from any edge, down to 360 by 400 pixels. At 760 pixels wide or less the page switches to its
compact layout (the same one a phone uses). The app remembers where you left the window and how big
it was, separately for each Tico app on the Mac, and opens there next time. It centres the window
only on the first launch, or when the saved place would leave its title bar off every connected display.
Then it opens on the primary display at the default size, 1240 by 820 points, or smaller where that
screen is too small for it, centred.

Tico puts nothing in the Mac's top menu bar. On macOS, closing the window hides it and the app
keeps running: click Tico's Dock icon to bring the window back, and use **Quit** (⌘Q) to quit.
The Dock icon's badge is how many things need you. **View → Search…** (⌘K) and
**View → Reload** (⌘R) are in the app's menu. On Windows and Linux, closing the window quits.
On macOS, **Open at Login** in the app's menu starts Tico when you log in. It is off until you turn it on,
and its check mark shows whether it is on. Each Tico app on the Mac has its own setting.

The app checks for signed updates automatically, installs them and relaunches. Generic builds
use the public GitHub release manifest; your server's web interface updates with the server.
It checks hourly and from **Tico > Check for Updates…**. When your server offers a newer app than the
one running, a bar at the top says so with an **Update** button; if the install fails, the bar shows
the reason and a download link. Settings and the foot of Help show the server version and the app version.

The app keeps a log file, `tico.log`, in its log folder (`~/Library/Logs/<bundle ID>/` on macOS), at info level: each
start, each update check and how it went, and any warning. At 1 MB it moves to `tico.log.1`, so two files at most are
kept. **Show the app's log** at the foot of Help opens it in Finder; a Support request sent from the app attaches its
last 200 lines with @diagnostics, redacted like the rest of the bundle.
Your team may also offer its own app.

### Switch between organizations (older app)

The generic Mac app can hold several organizations, each with its own sign-in. Once you have added more than one (see the first launch above), **Switch organization** appears in the account menu. It lists your organizations with a check mark on the open one; choose one to show its page. Switching needs no restart and no new sign-in.

When you leave an organization, its page is hidden and stays open for 30 seconds, so switching back shows it as it was, with no reload. Up to two hidden pages are kept this way. A page past that limit closes at once and opens again where you were when you return to it. A call in the page you leave ends as you switch. A page that may be capturing the screen closes as you leave it.

Choose **Remove *name*…** from the **Tico** menu to remove an organization. Tico asks first, then deletes its sign-in and its data on this Mac. The other organizations are not affected.

Only the generic Mac app can hold several organizations. Company builds, environment builds with a server address, and the Windows and Linux apps connect to one server at a time.

## Optional per-environment builds

`scripts/app.sh --env <slug>` still builds an app with that environment's name, icon, bundle
identifier, server address and hub updater endpoint. See [Environments](environments.md).
`TICO_HUB_URL` can also bake in a server address at build time. An address supplied in the
process environment as `HUB_URL` wins over the baked address, which wins over the saved address.
Without any of those addresses, the local first-launch page opens. Environment builds hide
**Change server…** and ignore attempts to change the saved address; their built-in server
retains priority. Built-in, process and saved addresses may use HTTP for existing deployments;
only a newly entered first-launch or **Change server…** address requires HTTPS or loopback HTTP.

Servers offer their company app whenever a bucket build exists, including when it is older than
the server. Without one, they offer desktop assets from their running GitHub release. If
GitHub cannot be reached, the download link waits until the next successful check; failures are
cached for ten minutes. Source checkouts reporting `dev` need a bucket build or a download from
GitHub directly.

A built-in server address is saved to `server.txt` when no saved address exists, so a later
generic build retains it if the bundle identifier is unchanged. CI uses `team.tico.app` for
both generic and hub builds. `scripts/app.sh --env` uses `team.tico.env.<environment id>`:
its config folder differs from the generic app, so installing the generic app separately
does not inherit that environment's settings. Environment updater feeds never offer generic
GitHub builds.

## Your company's app

Download your company's app from your own Tico page. It has your team's name and logo, opens
your Team directly, and updates itself from your server. Each company has a stable bundle ID,
so its app can coexist with another company's app and the generic Tico app. The download API
`GET /api/download/{mac|windows|linux}` returns `app_kind: company` or `generic`; a frontend
can show **Download Mac app** (or Windows, Linux) for a company build. Without a company build, the
answer is the generic app from the server's own GitHub release: the current app's installer when the
release has it, otherwise the older app's.

If an older company app does not update itself, sign in to your team's Tico page, download the
company app again and install it over the existing copy. Keep the app's saved data; its stable
bundle ID preserves the selected server and sign-in state. The replacement shell uses the signed-in
hub session for protected update files. Downloads remain behind Cloudflare Access.

Owners can set the public team logo in **Settings → General → Choose icon**, or with
`tico team icon logo.png`, using their owner credential
(`HUB_API_URL` and `HUB_TOKEN`). The API is `POST /api/v2/team/icon`, with an opaque binary PNG,
JPEG or WebP body up to 1 MB and an `Idempotency-Key`. Images up to 16 megapixels are normalized
to a square PNG, at most 1024 pixels per side and 1 MB, with transparent padding and metadata removed.
The answer is `{url: "/api/v2/team/icon", content_type: "image/png"}`. Only the owner may
write; `GET /api/v2/team/icon` is public, with a five-minute cache and ETag/304 support. It
returns 404 until a logo is set. This stable path is suitable for CI; expose it outside your
sign-in proxy, or use another public HTTPS PNG URL.
When blob storage uses S3, its server credential also needs DeleteObject for retired icon blobs;
a cleanup failure preserves the current icon and blocks another replacement until cleanup succeeds.

