namespace DocLink;

model Link {
  id: uuid;
  source_id: uuid;
  target_id: uuid;
  link_type: LinkType;
  origin: LinkOrigin;
  review_action_id: uuid;
  suggestion_id: uuid | null;
  created_at: utcDateTime;
  removed_at: utcDateTime | null;
  removal_action_id: uuid | null;
}
