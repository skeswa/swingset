#!/bin/bash
UA='swingset/0.0 (+https://github.com/skeswa/swingset)'
get() {
  local name=$1 url=$2
  shift 2
  curl -sS -L --max-time 60 -A "$UA" -H 'Accept-Encoding: gzip' --compressed \
    -D "$name.hdr" -o "$name.body" -w "%{http_code} %{size_download} %{time_total}s\n" "$@" "$url" >"$name.meta" 2>&1
  echo "== $name: $(cat $name.meta)"
}
etag_of() { grep -i '^etag:' "$1.hdr" | tail -1 | cut -d' ' -f2- | tr -d '\r'; }
lm_of() { grep -i '^last-modified:' "$1.hdr" | tail -1 | cut -d' ' -f2- | tr -d '\r'; }
(
  get dcn_results https://danceconvention.net/eventdirector/en/eventpage/301273270/results
  sleep 6
  et=$(etag_of dcn_results)
  get dcn_results_cond https://danceconvention.net/eventdirector/en/eventpage/301273270/results -H "If-None-Match: $et"
  sleep 6
  get dcn_robots https://danceconvention.net/robots.txt
) &
(
  get sd_event https://scoring.dance/enUS/events/418/results/
  sleep 6
  lm=$(lm_of sd_event)
  et=$(etag_of sd_event)
  get sd_event_cond https://scoring.dance/enUS/events/418/results/ -H "If-Modified-Since: $lm" ${et:+-H "If-None-Match: $et"}
  sleep 6
  get sd_round https://scoring.dance/enUS/events/418/results/ -o /dev/null -I
) &
(
  get wdr_awards_routeinfo https://scores.worlddanceregistry.com/98011277-01cd-11f1-9a29-0aa72bbce9ea/awards/routeInfo.json
  sleep 6
  et=$(etag_of wdr_awards_routeinfo)
  get wdr_awards_routeinfo_cond https://scores.worlddanceregistry.com/98011277-01cd-11f1-9a29-0aa72bbce9ea/awards/routeInfo.json -H "If-None-Match: $et"
) &
(
  get wsdc_events https://worldsdc.com/events/
  sleep 6
  et=$(etag_of wsdc_events)
  lm=$(lm_of wsdc_events)
  get wsdc_events_cond https://worldsdc.com/events/ ${et:+-H "If-None-Match: $et"} ${lm:+-H "If-Modified-Since: $lm"}
) &
wait
for f in dcn_results dcn_results_cond dcn_robots sd_event sd_event_cond wdr_awards_routeinfo wdr_awards_routeinfo_cond wsdc_events wsdc_events_cond; do
  echo "--- $f.hdr"
  grep -iE '^(HTTP/|etag|last-modified|cache-control|content-encoding|content-length|cf-cache-status|x-cache|age|expires)' "$f.hdr" | tr -d '\r'
done
echo "--- dcn robots"
cat dcn_robots.body
echo "--- dcn body sizes"
wc -c dcn_results.body dcn_results_cond.body
cmp dcn_results.body dcn_results_cond.body && echo IDENTICAL || echo DIFFER
grep -o 'publishCompResults[^,]*' dcn_results.body | head -2
echo "--- sd body"
wc -c sd_event.body sd_event_cond.body
cmp sd_event.body sd_event_cond.body && echo IDENTICAL || echo DIFFER
echo "--- wsdc body"
wc -c wsdc_events.body wsdc_events_cond.body
cmp wsdc_events.body wsdc_events_cond.body && echo IDENTICAL || echo DIFFER
