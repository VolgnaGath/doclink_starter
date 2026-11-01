namespace DocLink;

model Link {
  id: int64;
  source_id: uuid;
  target_id: uuid;
  link_type: LinkType;
  origin: LinkOrigin;
  review_action_id?: int64;
  suggestion_id?: int64;
  created_at: utcDateTime;
  removed_at?: utcDateTime;
}
