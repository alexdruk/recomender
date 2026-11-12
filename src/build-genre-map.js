'use strict';
// Milestone 2, part C, step 7. Builds data/genre-map.json, which maps every
// genre code to one of about 25 plain groups.
//
// There are two kinds of codes:
//   - the catalog codes (the classic FB2 list plus Flibusta's additions):
//     each one is listed by hand in GROUPS below
//   - free-form codes found inside feed files (boyar-anime, fentezi, ...):
//     these are matched by the word-piece RULES below
// A code that fits neither is left out of the map and listed under "unmapped".
//
// To change the mapping, edit GROUPS or RULES here and run this script again.

const fs = require('node:fs');
const path = require('node:path');
const Database = require('better-sqlite3');
const c = require('./common');

// kind: "fiction", "nonfiction", or "neutral" (says nothing about fiction or not)
const GROUPS = [
  ['fantasy', 'fiction', `sf_fantasy sf_fantasy_city russian_fantasy sf_technofantasy fairy_fantasy asian_fantasy everyday_fantasy
    historical_fantasy magic_school adventure_fantasy sf_industrial_magic slavic_fantasy dark_fantasy sf_heroic sf_epic dorama
    modern_tale foreign_fantasy fantasy magician_book wuxia`],
  ['romantic fantasy', 'fiction', 'love_sf love_fantasy'],
  ['litrpg', 'fiction', 'sf_litrpg sf_realrpg litrpg realrpg'],
  ['popadantsy', 'fiction', 'popadancy popadanec popadantsy'],
  ['boyar-anime', 'fiction', 'boyar_anime'],
  ['science fiction', 'fiction', `sf sf_space sf_social sf_cyberpunk sf_postapocalyptic sf_stimpank hronoopera dystopian utopia nsf
    foreign_sf sf_su sf_action sf_etc`],
  ['alternative history', 'fiction', 'sf_history'],
  ['detective', 'fiction', `det_classic det_police det_action det_irony det_history det_espionage det_crime det_political det_hard
    detective det_su det_artifact det_lady det_other sf_detective fantasy_det love_detective foreign_detective`],
  ['thriller', 'fiction', 'thriller det_maniac'],
  ['horror and mystic', 'fiction', 'sf_horror sf_mystic gothic_novel vampire_book'],
  ['contemporary romance', 'fiction', 'love_contemporary love_short love foreign_love'],
  ['erotica', 'fiction', 'love_erotica love_hard erotica home_sex'],
  ['historical', 'fiction', 'prose_history adv_history love_history'],
  ['adventure', 'fiction', 'adventure adv_indian adv_maritime adv_geo adv_animal adv_modern adv_story tale_chivalry foreign_adventure'],
  ['classic prose', 'fiction', `prose_classic prose_rus_classic prose_su_classics literature_18 literature_19 literature_20 foreign_antique
    antique_ant antique_european antique_russian antique_east antique`],
  ['contemporary prose', 'fiction', `prose_contemporary prose foreign_prose foreign_contemporary prose_counter prose_magic prose_neformatny
    epistolary_fiction aphorisms great_story story prose_abs prose_game`],
  ['war prose', 'fiction', 'prose_military'],
  ['humour', 'fiction', 'humor humor_prose humor_satire humor_anecdote sf_humor humor_fantasy foreign_humor'],
  ['children', 'fiction', `child_education children child_classical foreign_children child_folklore child_tale child_tale_rus folk_tale
    child_tale_foreign_writers child_tale_russian_writers child_verse child_prose child_sf child_det child_adv child_prose_humor
    child_prose_history child_adv_animal child_prose_romantic child_sf_space child_sf_hronoopera child_sf_horror child_sf_fantasy
    child_det_children_detectives child_det_animal_detectives child_det_other child_dramaturgy`],
  ['poetry and drama', 'fiction', `poetry humor_verse poetry_classical poetry_modern poetry_rus_classical poetry_rus_modern poetry_for_classical
    poetry_for_modern poetry_east lyrics song_poetry poem palindromes dramaturgy comedy tragedy drama drama_antique screenplays vaudeville
    foreign_poetry foreign_dramaturgy`],
  ['folklore', 'fiction', 'antique_myths folklore epic proverbs folk_songs limerick'],
  ['web literature', 'neutral', 'network_literature fanfiction unfinished'],
  ['fan translation', 'neutral', 'fan_translation computer_translation llm_art'],
  ['nonfiction', 'nonfiction', `banking org_behavior popular_business economics_ref economics child_psychology
    nonf_biography nonf_publicism nonfiction nonf_military military_special travel_notes nonf_biography_military_figures
    nonf_biography_celebrities nonf_biography_historical about_musicians nonf_biography_writers auto_biography
    sci_psychology_popular home_cooking home_pets home_crafts home_entertain home_health home_garden home_diy home_sport home
    sci_pedagogy auto_regulations home_collecting family
    sci_culture notes nonf_criticism design music painting architecture_book art_world_culture cine theatre art_criticism
    comp_www comp_hard comp_db computers tbg_computers
    sci_history sci_psychology sci_philosophy sci_politics sci_juris sci_linguistic sci_medicine sci_phys sci_math sci_chem sci_biology
    science sci_cosmos sci_geo sci_state sci_economy sci_medicine_alternative sci_philology sci_popular military_history
    sci_social_studies sci_zoo sci_botany sci_ecology sci_oriental sci_theories sci_veterinary
    sci_religion religion_budda religion_esoterics religion_self religion religion_christianity religion_orthodoxy religion_protestantism
    religion_catholicism religion_judaism religion_hinduism religion_islam religion_paganism astrology
    ref_encyc ref_dict ref_ref ref_guide reference geo_guides
    sci_tech sci_build sci_radio sci_metal sci_transport military_weapon auto_business equ_history
    sci_textbook tbg_school tbg_secondary tbg_higher`],
  ['other', 'neutral', 'other periodic comics'],
];

// Word-piece rules for free-form codes. The first rule that matches wins.
// They are tested on the code in lower case with "-" and spaces turned into "_".
const RULES = [
  [/^(sci_fi|science_fiction)/, 'science fiction'],
  [/nauchn|populyar|medicin|management|^psy(_|$)/, 'nonfiction'],
  [/^(sci|nonf|nonfiction|ref|comp|religion|home|science|military_history|geo|economics|business|psychology|health|design|music|cine|theatre|art|painting|architecture|sport|auto|travel_notes|cooking|pedagogy|biograf|publicist|foreign_(business|psychology|edu|publicism|language|religion|desc|comp|home|other))(_|$)/, 'nonfiction'],
  [/rpg/, 'litrpg'],
  [/boyar/, 'boyar-anime'],
  [/popadan|back_to_ussr|nazad_v_sssr/, 'popadantsy'],
  [/(love|lyubov|liubov|romanti|romance).*(fantas|fentezi|fantastik|sf)|(fantas|fentezi).*(love|lyubov|liubov|romanti|romance)/, 'romantic fantasy'],
  [/erot/, 'erotica'],
  [/horror|uzhas|mistik|mystic|vampir/, 'horror and mystic'],
  [/thriller|triller/, 'thriller'],
  [/^det(_|$)|detectiv|detektiv|crime|kriminal|mystery/, 'detective'],
  [/humor|yumor|ironi|comedy|satir/, 'humour'],
  [/sf_history|alternativ/, 'alternative history'],
  [/fentezi|fantasy|magic|magi[iy]|wuxia|cultivation|anime/, 'fantasy'],
  [/histor|istori/, 'historical'],
  [/^sf(_|$)|fantastik|science_fiction|sci_fi|kosm|space|cyberpunk|kiberpank|steampunk|stimpank|postapo|dystop/, 'science fiction'],
  [/love|lyubov|liubov|lubov|romance|romany|roman(_|$)/, 'contemporary romance'],
  [/^adv(_|$)|adventur|prikl|morskie|action|boevik/, 'adventure'],
  [/voenn|military|(^|_)war(_|$)/, 'war prose'],
  [/child|detsk|skazk|tale/, 'children'],
  [/poet|poezi|stihi|lyrics|drama|tragedy/, 'poetry and drama'],
  [/klassi|classic/, 'classic prose'],
  [/proz|prose|story|rasskaz|novell|sovremenn|contemporary|rusreal|fiction|sagas/, 'contemporary prose'],
  [/samizdat|network|fanfic/, 'web literature'],
];

function normCode(code) {
  return String(code).toLowerCase().trim().replace(/[-\s]+/g, '_');
}

function buildMap(codes) {
  const exact = new Map();
  for (const [group, , list] of GROUPS) for (const code of list.split(/\s+/).filter(Boolean)) exact.set(code, group);
  const map = {};
  const unmapped = [];
  for (const code of [...new Set([...exact.keys(), ...codes])].sort()) {
    const n = normCode(code);
    let group = exact.get(code) || exact.get(n);
    if (!group) {
      const rule = RULES.find(([re]) => re.test(n));
      if (rule) group = rule[1];
    }
    if (group) map[code] = group;
    else unmapped.push(code);
  }
  return { map, unmapped };
}

// Every genre code we know of: the catalog list, plus the codes found in book files.
function collectCodes(db, cfg) {
  const codes = new Set();
  for (const r of db.prepare('SELECT genres FROM parsed WHERE genres IS NOT NULL').all()) for (const g of c.jsonList(r.genres)) codes.add(g);
  if (fs.existsSync(cfg.catalogPath)) {
    const cat = new Database(cfg.catalogPath, { readonly: true });
    for (const r of cat.prepare('SELECT GenreCode FROM libgenrelist').all()) codes.add(r.GenreCode);
    cat.close();
  }
  return [...codes].filter(Boolean);
}

function build(db, cfg) {
  const { map, unmapped } = buildMap(collectCodes(db, cfg));
  const out = {
    note: 'Generated by src/build-genre-map.js. Do not edit by hand; edit the lists in that script and run it again.',
    groups: Object.fromEntries(GROUPS.map(([name, kind]) => [name, kind])),
    codes: map,
    unmapped,
  };
  fs.writeFileSync(cfg.genreMapPath, JSON.stringify(out, null, 2) + '\n', 'utf8');
  return out;
}

async function main() {
  const cfg = c.loadConfig();
  const db = c.openDb(cfg);
  const out = build(db, cfg);
  console.log(`Genre map: ${Object.keys(out.codes).length} codes mapped to ${Object.keys(out.groups).length} groups, ${out.unmapped.length} unmapped -> ${path.relative(cfg.root, cfg.genreMapPath)}`);
  if (out.unmapped.length) console.log('  unmapped: ' + out.unmapped.join(', '));
  db.close();
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e.message || e);
    process.exit(1);
  });
}

module.exports = { main, build, normCode };
