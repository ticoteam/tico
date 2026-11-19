-- Products (Company › Products): the fields a product of ours carries, and the two links that join it to the legal and
-- vendor records. Rows only, built in; a hub that already has one of these keys keeps its own (INSERT OR IGNORE).
INSERT OR IGNORE INTO entity_attributes (id, entity_type, key, name, value_type, multiple, unique_value, required, options,
    owner_type, owner_id, description, sensitive, position, created_by_type, created_by_id, created_at)
VALUES
('attr_product_kind', 'product', 'kind', 'Kind', 'select', 0, 0, 0,
 '[{"key": "software", "name": "Software"}, {"key": "service", "name": "Service"}, {"key": "goods", "name": "Goods"}]',
 'tico', '', 'What the product is: software, a service or goods.', 0, 1, 'system', '', '2000-01-01T00:00:00.000000Z'),
('attr_product_status', 'product', 'status', 'Status', 'select', 0, 0, 0,
 '[{"key": "planned", "name": "Planned"}, {"key": "beta", "name": "Beta"}, {"key": "live", "name": "Live"}, {"key": "retired", "name": "Retired"}]',
 'tico', '', 'Where the product is in its life.', 0, 2, 'system', '', '2000-01-01T00:00:00.000000Z'),
('attr_product_tax_kind', 'product', 'tax_kind', 'Tax kind', 'select', 0, 0, 0,
 '[{"key": "software_service", "name": "Software"}, {"key": "service", "name": "Service"}, {"key": "goods", "name": "Goods"}, {"key": "digital_goods", "name": "Digital goods"}, {"key": "not_taxed", "name": "Not taxed"}, {"key": "unknown", "name": "Unknown"}]',
 'tico', '', 'How sales tax treats the product. It decides which tax rules read it.', 0, 3, 'system', '', '2000-01-01T00:00:00.000000Z'),
('attr_product_owner_teammate', 'product', 'owner_teammate', 'Owner', 'actor', 0, 0, 0, '[]',
 'tico', '', 'The teammate (a person or a bot) who owns the product.', 0, 4, 'system', '', '2000-01-01T00:00:00.000000Z'),
('attr_product_repositories', 'product', 'repositories', 'Code', 'text', 1, 0, 0, '[]',
 'tico', '', 'The repositories the product is built from (owner/name).', 0, 5, 'system', '', '2000-01-01T00:00:00.000000Z');

INSERT OR IGNORE INTO link_types (id, key, name, inverse_name, from_entity_type, to_entity_type, inherits_rules, symmetric,
    description, built_in, created_by_type, created_by_id, created_at)
VALUES
('ltype_sold_under', 'sold_under', 'sold under', 'governs sales of', 'product', 'document', 0, 0,
 'The agreement a product is sold under: its terms of service or customer agreement.', 1, 'system', '',
 '2000-01-01T00:00:00.000000Z'),
('ltype_depends_on', 'depends_on', 'depends on', 'supports', 'product', 'organization', 0, 0,
 'A vendor the product needs to run: a host, a payment processor, an email service.', 1, 'system', '',
 '2000-01-01T00:00:00.000000Z');
