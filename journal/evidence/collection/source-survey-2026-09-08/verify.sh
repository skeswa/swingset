#!/bin/bash
# Polite verification fetches: one host per subshell, sequential within a host, 6 s gaps.
UA='swingset/0.0 (+https://github.com/skeswa/swingset)'
get() { # name url [extra curl args...]
  local name=$1 url=$2
  shift 2
  curl -sS -L --max-time 30 -A "$UA" -H 'Accept-Encoding: gzip, br' --compressed \
    -D "$name.hdr" -o "$name.body" -w "%{http_code} %{size_download} %{time_total}s %{url_effective}\n" "$@" "$url" >"$name.meta" 2>&1
  echo "== $name: $(cat $name.meta)"
}
etag_of() { grep -i '^etag:' "$1.hdr" | tail -1 | cut -d' ' -f2- | tr -d '\r'; }
lm_of() { grep -i '^last-modified:' "$1.hdr" | tail -1 | cut -d' ' -f2- | tr -d '\r'; }

( # eepro
  get eepro_round https://eepro.com/results/summerhummer2026/jjprelims.html
  sleep 6
  et=$(etag_of eepro_round)
  lm=$(lm_of eepro_round)
  get eepro_round_cond https://eepro.com/results/summerhummer2026/jjprelims.html -H "If-None-Match: $et" -H "If-Modified-Since: $lm"
  sleep 6
  get eepro_dir https://eepro.com/results/summerhummer2026/
  sleep 6
  get eepro_index https://eepro.com/results/event.php
  sleep 6
  et=$(etag_of eepro_index)
  lm=$(lm_of eepro_index)
  get eepro_index_cond https://eepro.com/results/event.php -H "If-None-Match: $et" -H "If-Modified-Since: $lm"
) &
( # scoring.dance
  get sd_event https://scoring.dance/enUS/events/418/results/
  sleep 6
  et=$(etag_of sd_event)
  lm=$(lm_of sd_event)
  get sd_event_cond https://scoring.dance/enUS/events/418/results/ -H "If-Modified-Since: $lm" ${et:+-H "If-None-Match: $et"}
  sleep 6
  get sd_sitemap https://scoring.dance/sitemap.xml
  sleep 6
  get sd_robots https://scoring.dance/robots.txt
) &
( # dcn
  get dcn_results https://danceconvention.net/eventdirector/en/eventpage/301273270/results
  sleep 6
  et=$(etag_of dcn_results)
  lm=$(lm_of dcn_results)
  get dcn_results_cond https://danceconvention.net/eventdirector/en/eventpage/301273270/results -H "If-None-Match: $et"
  sleep 6
  get dcn_pdf_head https://danceconvention.net/eventdirector/en/roundscores/301638270.pdf -I
  sleep 6
  get dcn_robots https://danceconvention.net/robots.txt
) &
( # wdr
  U=98011277-01cd-11f1-9a29-0aa72bbce9ea
  get wdr_awards_routeinfo "https://scores.worlddanceregistry.com/$U/awards/routeInfo.json"
  sleep 6
  get wdr_awards "https://scores.worlddanceregistry.com/$U/awards"
  sleep 6
  et=$(etag_of wdr_awards)
  lm=$(lm_of wdr_awards)
  get wdr_awards_cond "https://scores.worlddanceregistry.com/$U/awards" -H "If-None-Match: $et"
  sleep 6
  get wdr_rounds_routeinfo "https://scores.worlddanceregistry.com/$U/rounds/routeInfo.json"
  sleep 6
  get wdr_root_routeinfo "https://scores.worlddanceregistry.com/$U/routeInfo.json"
) &
( # wsdc calendar
  get wsdc_events https://worldsdc.com/events/
  sleep 6
  et=$(etag_of wsdc_events)
  lm=$(lm_of wsdc_events)
  get wsdc_events_cond https://worldsdc.com/events/ ${et:+-H "If-None-Match: $et"} ${lm:+-H "If-Modified-Since: $lm"}
  sleep 6
  get wsdc_robots https://worldsdc.com/robots.txt
) &
wait
echo
echo "### headers of interest"
for f in *.hdr; do
  echo "--- $f"
  grep -iE '^(HTTP/|etag|last-modified|cache-control|content-encoding|content-type|content-length|cf-cache-status|x-cache|server|vary|age|x-amz|via|retry-after|ratelimit)' "$f" | tr -d '\r'
done
