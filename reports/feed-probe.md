# Feed probe

Built from `data/books.db` by `src/probe-feed.js`. One daily update file from Flibusta was parsed with the same FB2 parser as the library. All feed books carry the label `unlabeled`.

## The file

| What | Value |
| --- | --- |
| File | `f.fb2.895576-895677.zip` |
| How it was obtained | already downloaded from https://flibusta.is |
| Zip size | 95,421,091 bytes (91.0 MB) |
| Files inside | 86 |
| Files by extension | .fb2: 86 |
| Unpacked size | 160.0 MB |
| Average size of one book | 1084 KB zipped, 1905 KB unpacked |

## How file names map to book ids

- The zip is named `f.fb2.<first id>-<last id>.zip`. This one covers ids 895576 to 895677, a span of 102 ids.
- Inside, each book is one file named `<book id>.fb2`. 86 of 86 files follow this pattern (for example `895576.fb2`, `895577.fb2`, `895578.fb2`).
- 86 of those ids fall inside the range in the zip name; lowest 895576, highest 895677.
- 16 ids in the range have no file here (15.7%). Those are books in other formats (they go into the matching `f.n.*` zip) or ids that were never published.
- The id is the same number Flibusta uses on its site (`/b/<id>`) and in the names of the library files, so the database key for a feed book is simply that id.

## Parsing

| What | Count |
| --- | --- |
| Book files | 86 |
| Parsed | 86 (100.0%) |
| ... of which needed the lenient parser | 0 |
| Failed | 0 |
| Duplicates inside the file | 0 |
| Rows in `feed_books` | 86 |

## Parse time per book

| What | Time |
| --- | --- |
| Average per book | 10.6 ms |
| Median per book | 6.8 ms |
| Slowest book | 104.0 ms |
| All books, parsing only | 0.91 s |
| Whole file, including unzip and saving | 2.12 s |
| Speed | 34,880,335 characters of book text per second |

Parsing covers decoding the file, reading the XML, and pulling out metadata and body text.

## Language distribution

| Language | Books | Share |
| --- | --- | --- |
| ru | 86 | 100.0% |

## Genre distribution

A book can have several genres, so the counts add up to more than the number of books. The last column shows how this project reads each code. The feed mixes the classic FB2 codes (`sf_fantasy`, `det_classic`) with newer free-form ones (`boyar-anime`, `fentezi`).

| Genre code | Books | Share of books | Read as |
| --- | --- | --- | --- |
| love_contemporary | 9 | 10.5% | fiction |
| love_sf | 9 | 10.5% | fiction |
| love_short | 9 | 10.5% | fiction |
| boyar-anime | 7 | 8.1% | fiction |
| love_erotica | 7 | 8.1% | fiction |
| popadantsy-v-magicheskie-miry | 7 | 8.1% | fiction |
| prose_classic | 6 | 7.0% | fiction |
| fantasy-action | 5 | 5.8% | fiction |
| popadanec | 5 | 5.8% | fiction |
| realrpg | 5 | 5.8% | fiction |
| adventure | 4 | 4.7% | fiction |
| popadantsy | 4 | 4.7% | fiction |
| sf_horror | 4 | 4.7% | fiction |
| nonf_biography | 3 | 3.5% | nonfiction |
| popadantsy-vo-vremeni | 3 | 3.5% | fiction |
| sf | 3 | 3.5% | fiction |
| sf-action | 3 | 3.5% | fiction |
| sf-space | 3 | 3.5% | fiction |
| sf_fantasy | 3 | 3.5% | fiction |
| back-to-ussr | 2 | 2.3% | fiction |
| detective | 2 | 2.3% | fiction |
| fentezi | 2 | 2.3% | fiction |
| foreign_fantasy | 2 | 2.3% | fiction |
| humor | 2 | 2.3% | fiction |
| prose_military | 2 | 2.3% | fiction |
| sci_culture | 2 | 2.3% | nonfiction |
| sf-history | 2 | 2.3% | fiction |
| wuxia | 2 | 2.3% | fiction |
| action | 1 | 1.2% | fiction |
| adv_history | 1 | 1.2% | fiction |
| cine | 1 | 1.2% | nonfiction |
| design | 1 | 1.2% | nonfiction |
| det_classic | 1 | 1.2% | fiction |
| det_irony | 1 | 1.2% | fiction |
| erotica | 1 | 1.2% | fiction |
| fantastika | 1 | 1.2% | fiction |
| fantasy | 1 | 1.2% | fiction |
| great_story | 1 | 1.2% | fiction |
| heroic-fantasy | 1 | 1.2% | fiction |
| historical-adventure | 1 | 1.2% | fiction |
| historical-fantasy | 1 | 1.2% | fiction |
| historical-romance | 1 | 1.2% | fiction |
| home_health | 1 | 1.2% | nonfiction |
| humor_fantasy | 1 | 1.2% | fiction |
| ironical-fantasy | 1 | 1.2% | fiction |
| litrpg | 1 | 1.2% | fiction |
| liubovnye-romany | 1 | 1.2% | fiction |
| love_fantasy | 1 | 1.2% | fiction |
| lyubovnoe | 1 | 1.2% | fiction |
| magician_book | 1 | 1.2% | fiction |
| music | 1 | 1.2% | nonfiction |
| nonf_biography_writers | 1 | 1.2% | nonfiction |
| priklyucheniya | 1 | 1.2% | fiction |
| prose_contemporary | 1 | 1.2% | fiction |
| prose_history | 1 | 1.2% | fiction |
| prose_su_classics | 1 | 1.2% | fiction |
| proza | 1 | 1.2% | fiction |
| religion_self | 1 | 1.2% | nonfiction |
| rusreal | 1 | 1.2% | fiction |
| sci_history | 1 | 1.2% | nonfiction |
| sci_psychology | 1 | 1.2% | nonfiction |
| sci_psychology_popular | 1 | 1.2% | nonfiction |
| science-fiction | 1 | 1.2% | nonfiction |
| sf-social | 1 | 1.2% | fiction |
| sf_detective | 1 | 1.2% | fiction |
| sf_epic | 1 | 1.2% | fiction |
| sf_heroic | 1 | 1.2% | fiction |
| sf_humor | 1 | 1.2% | fiction |
| sf_social | 1 | 1.2% | fiction |
| steampunk | 1 | 1.2% | fiction |
| story | 1 | 1.2% | fiction |
| theatre | 1 | 1.2% | nonfiction |
| thriller | 1 | 1.2% | fiction |
| voennaia-literatura | 1 | 1.2% | fiction |

Books with no genre: 3.

## Russian-language fiction

A book is fiction when it has at least one fiction genre code (science fiction, fantasy, detective, prose, romance, adventure, humour, poetry, drama, children's stories) and no non-fiction code (science, biography, reference, religion, home, arts). Books with no genre at all are not counted as fiction. It is Russian-language when its `lang` field is `ru`.

| Group | Books | Share of parsed books |
| --- | --- | --- |
| All parsed books | 86 | 100.0% |
| Russian-language | 86 | 100.0% |
| Fiction, any language | 74 | 86.0% |
| **Russian-language fiction** | **74** | **86.0%** |
| ... written in Russian | 53 | 61.6% |
| ... translated into Russian | 21 | 24.4% |

## Overlap with the archive

A feed book duplicates an archive book when it has the same Flibusta id, or the same normalised author + title.

- Feed books that duplicate an archive book: **0 of 86 (0.0%)**.
- Among Russian-language fiction: 0 of 74 (0.0%).

## Candidates to expect per update file

After filtering to Russian-language fiction and removing books already in the archive, **this update file gives 74 candidate books** out of 86 files (86.0%).

The mirror listed 5 update files at the time of the probe. Their id ranges span 50 to 132 ids (this file: 102). If the other files have the same mix as this one, that means roughly **36 to 96 candidates per update file**, about 64 on average.

| Update file | Ids in range | Estimated candidates |
| --- | --- | --- |
| f.fb2.895239-895370.zip | 132 | about 96 |
| f.fb2.895371-895445.zip | 75 | about 54 |
| f.fb2.895446-895495.zip | 50 | about 36 |
| f.fb2.895496-895575.zip | 80 | about 58 |
| f.fb2.895576-895677.zip (probed) | 102 | 74 (measured) |

This is a measurement of one file. The number will move from day to day; a few more files are needed before trusting it to better than a factor of two.

First 15 candidates, as a sanity check:

| Key | Author | Title | Genres | Words |
| --- | --- | --- | --- | --- |
| 895577 | Фолькер Браун, Ангела Стахова, Вальтрауд Арндт, Хельга Кёнигсдорф, Вольфганг де Бройн, Кристоф Хайн, Мартин Штефан, Хельга Шуберт, Эрих-Гюнтер Зассе, Эльфрида Брюнинг, Вольфганг Трампе, Вернер Ленц, Хорст Маттис, Юрий Кох | Неоконченная история | prose_contemporary | 92,618 |
| 895580 | Айза Таллер | Обсидиановый огонь | priklyucheniya, fantastika, fentezi, voennaia-literatura | 135,528 |
| 895581 | Уильям Уилки Коллинз | Лунный камень | det_classic, adventure | 265,980 |
| 895583 | Майкл Патрик Хикс | Револьвер | sf_social | 9,720 |
| 895584 | Майкл Патрик Хикс | Черный сектор | sf_horror | 8,339 |
| 895585 | Джон Кристофер | Избранник судьбы | sf | 2,528 |
| 895586 | Баковец Михаил | Творец железных птиц 2 | popadantsy | 2,825 |
| 895590 | Олег Евгеньевич Абахов | Империя Окоф | sf_horror | 50,659 |
| 895592 | Ж. К. ДеРоса | Демоны | sf_heroic, magician_book, foreign_fantasy | 58,992 |
| 895593 | Ханна Николь Мерер | Сообщница Злодея | love_fantasy, sf_detective, humor_fantasy, foreign_fantasy | 103,081 |
| 895597 | Стивен Стэйси, Золтан Комор, Марк Люкенс, Дж. К. Майкл | Коллекция ужасов: Тыквенное издание | sf_horror | 13,197 |
| 895599 | Шэйн МакКензи | Гаражная распродажа Эда Гейна и другие зверства | sf_horror | 29,774 |
| 895600 | Елена Кара | Сжечь ведьму! Или согреть её сердце! | proza, fentezi, lyubovnoe, liubovnye-romany | 15,169 |
| 895602 | Таис Буше | Магнит на холодильник | love_short | 16,276 |
| 895603 | Харли Напьер, Глория Макова | Развод и девичья фамилия | love_erotica, love_short | 12,428 |
