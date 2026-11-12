//! Microsoft Teams on the web: its AX projection (captured live in Chrome, Nov 2026). Each video
//! tile is a menu item; a remote person's tile holds empty overlay groups, and one of them gains
//! the `vdi-frame-occlusion` class while that person is heard. The local person's tile has no
//! overlay at all. Teams' hashed `___` classes change between builds and are never used.
use super::{MeetingAdapter, Observation, meet::Node, name};
use std::collections::BTreeSet;

/// Every class the adapter relies on. A missing structure yields no evidence.
pub struct Classes {
    /// Video tile (an AXMenuItem); its AXDescription is the person's name. An atomic style
    /// class (the hash of one CSS rule): steadier than the `___` ones, but the first to recheck
    /// when Teams names stop.
    pub tile: &'static str,
    /// Fluent UI's flex container. A tile's speaking overlay is an empty one.
    pub overlay: &'static str,
    /// On an overlay while that tile's person speaks.
    pub speaking: &'static str,
}
pub const CLASSES: Classes = Classes {
    tile: "f1euv43f",
    overlay: "fui-Flex",
    speaking: "vdi-frame-occlusion",
};
const HOSTS: &[&str] = &[
    "teams.microsoft.com",
    "teams.live.com",
    "teams.cloud.microsoft",
];

pub struct Teams;

fn has(node: &Node, class: &str) -> bool {
    node.classes.iter().any(|c| c == class)
}
pub fn kept_class(class: &str) -> bool {
    [CLASSES.tile, CLASSES.overlay, CLASSES.speaking].contains(&class)
}
pub fn is_tile(node: &Node) -> bool {
    node.role == "AXMenuItem" && has(node, CLASSES.tile)
}
pub fn window_title(title: &str) -> bool {
    let title = title.to_lowercase();
    title.contains("microsoft teams") || HOSTS.iter().any(|host| title.contains(host))
}
pub fn private_branch(role: &str, label: &str) -> bool {
    matches!(
        role,
        "AXTextArea" | "AXTextField" | "AXWebArea" | "AXDocument"
    ) || ["Message List", "Compose", "Captions", "Live captions"]
        .iter()
        .any(|s| label.eq_ignore_ascii_case(s))
}

#[derive(Clone, Debug, PartialEq)]
pub struct Tile {
    pub name: String,
    /// Empty overlay groups in the tile; the local person's tile has none.
    pub overlays: usize,
    /// An overlay carries the speaking class.
    pub speaking: bool,
}
/// A shared screen is an extra tile under the presenter's name, with zoom controls.
fn shared(node: &Node) -> bool {
    node.role == "AXToolbar"
        || (node.role == "AXButton"
            && [&node.label, &node.description]
                .iter()
                .any(|l| l.eq_ignore_ascii_case("Zoom out")))
        || node.children.iter().any(shared)
}
fn overlay(node: &Node) -> bool {
    node.role == "AXGroup"
        && has(node, CLASSES.overlay)
        && node.children.is_empty()
        && node.label.is_empty()
        && node.description.is_empty()
}
/// State Teams appends to a tile's label: guests' "Unverified", and "More options" on hover.
fn clean(text: &str) -> &str {
    let mut text = text.trim();
    while let Some(rest) = [" Unverified", " More options"]
        .iter()
        .find_map(|suffix| text.strip_suffix(suffix))
    {
        text = rest.trim_end();
    }
    text
}
fn tile(node: &Node) -> Option<Tile> {
    if shared(node) {
        return None;
    }
    let described = clean(if node.description.is_empty() {
        &node.label
    } else {
        &node.description
    });
    // The tile's own name text is the bare display name; its label can lead with state, such as
    // "Unable to play media. ". That text is used only when it ends the label as a whole word.
    fn texts<'a>(node: &'a Node, out: &mut Vec<&'a str>) {
        if node.role == "AXStaticText" {
            out.push(node.label.trim());
        }
        for child in &node.children {
            texts(child, out);
        }
    }
    let mut found = vec![];
    texts(node, &mut found);
    let shown = found
        .into_iter()
        .find(|text| {
            !text.is_empty()
                && described.strip_suffix(text).is_some_and(|before| {
                    before.is_empty() || before.ends_with(char::is_whitespace)
                })
        })
        .unwrap_or(described);
    fn count(node: &Node, tile: &mut Tile) {
        if overlay(node) {
            tile.overlays += 1;
            tile.speaking |= has(node, CLASSES.speaking);
        }
        for child in &node.children {
            count(child, tile);
        }
    }
    let mut out = Tile {
        name: name(shown)?,
        overlays: 0,
        speaking: false,
    };
    node.children
        .iter()
        .for_each(|child| count(child, &mut out));
    Some(out)
}
/// Video tiles in tree order, without shared screens. Keyed by name: tiles move on relayout.
pub fn tiles(root: &Node) -> Vec<Tile> {
    fn visit(node: &Node, out: &mut Vec<Tile>) {
        if private_branch(&node.role, &node.label) || out.len() >= 200 {
            return;
        }
        if is_tile(node) {
            out.extend(tile(node));
            return;
        }
        for child in &node.children {
            visit(child, out);
        }
    }
    let mut out = vec![];
    visit(root, &mut out);
    out
}
/// The local person's tile is the one tile without a speaking overlay when every other tile has
/// one (the captured layout; Teams shows no "(You)" text on tiles). Otherwise no one.
fn local_tiles(tiles: &[Tile]) -> (Vec<bool>, &'static str) {
    let bare = tiles.iter().filter(|t| t.overlays == 0).count();
    if tiles.len() >= 2 && bare == 1 {
        return (tiles.iter().map(|t| t.overlays == 0).collect(), "structure");
    }
    (vec![], if tiles.is_empty() { "" } else { "none" })
}
impl MeetingAdapter for Teams {
    fn app(&self) -> &'static str {
        "Teams"
    }
    fn window_title(&self, title: &str) -> bool {
        window_title(title)
    }
    /// Teams keeps one address for its whole app, calls included: any Teams page qualifies,
    /// and [`MeetingAdapter::in_call`] tells a call apart by its tiles.
    fn detect_window(&self, _title: &str, document_url: &str) -> bool {
        reqwest::Url::parse(document_url).is_ok_and(|url| {
            url.scheme() == "https" && url.host_str().is_some_and(|host| HOSTS.contains(&host))
        })
    }
    fn kept_class(&self, class: &str) -> bool {
        kept_class(class)
    }
    fn name_container(&self, node: &Node) -> bool {
        is_tile(node)
    }
    fn private_branch(&self, role: &str, label: &str) -> bool {
        private_branch(role, label)
    }
    fn in_call(&self, observed: &Observation) -> bool {
        observed.rows > 0
    }
    fn observe(&self, root: &Node) -> Observation {
        let tiles = tiles(root);
        let (local, local_by) = local_tiles(&tiles);
        let locals: BTreeSet<_> = tiles
            .iter()
            .zip(&local)
            .filter(|(_, local)| **local)
            .map(|(t, _)| t.name.clone())
            .collect();
        // Without knowing the local tile, any speaker could be the mic: names, but no speaking.
        let known = !local.is_empty();
        let active: BTreeSet<_> = tiles
            .iter()
            .filter(|t| known && t.speaking && !locals.contains(&t.name))
            .map(|t| t.name.clone())
            .collect();
        let participants: BTreeSet<_> = tiles.iter().map(|t| t.name.clone()).collect();
        Observation {
            participants: participants.into_iter().take(100).collect(),
            self_names: locals.into_iter().collect(),
            active: active.into_iter().take(100).collect(),
            indicators: known && tiles.iter().any(|t| t.overlays > 0),
            rows: tiles.len(),
            local_by,
        }
    }
}
