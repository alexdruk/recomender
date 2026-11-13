# The selection funnel

Built by `model/funnel.py` (run everything with `npm run funnel`). Milestone 3c copies how I really choose books. **Decision 1**, before reading: should I try this book? (reader rating, reader comments, the series rule, authors I follow). **Decision 2**, while reading: do I like it? (style and plot of the first 100 pages). My liked books passed both.

## The plain answers

1. **Do reader comments exist? Yes.** The catalog has a dump `lib.reviews.sql.gz`: 823,860 comments on 191,395 books, each with the reader's name, the full text and a date and time (2007-09-23 to 2026-11-13). 301 of my 309 liked books have comments (median 13 per book).

2. **Does my series rule hold in my own library? Mostly, not fully.** Of my 309 liked books, 87 are standalone, 108 are number 1 of a series, and 82 are a later book of a series. Of those 82 later books, 56 have an earlier book of the same series among my liked books, and **26 do not** (8.4% of the library): there the library holds a later book without an earlier one. For 32 more the catalog gives no number (most are whole-series bundles). So the rule "never start a series from a later book" fits about nine liked books in ten; the exceptions are listed below.

3. **Which signals beat the noise?** Tested on 190 liked books against 430 eligible rated-matched books (standalone or first in a series, on both sides), adding one signal at a time: **series length**: no (+0.002 ± 0.011); **like-minded readers**: no (+0.006 ± 0.014); **item-to-item**: no (-0.001 ± 0.011); **reader comments**: no (+0.009 ± 0.014). Not beating the noise: series length, like-minded readers, item-to-item, reader comments. Best model: **rb+s+lm+c**, ROC AUC 0.782 ± 0.049 (rb alone: 0.764 ± 0.052). The "author on my list" signal cannot be tested this way, see below.

"Beats the noise" means: the average gain over the 50 test folds is bigger than the spread of that gain from fold to fold.

**Votes and comments on replaced copies.** When a book file is replaced by a newer copy, the site keeps showing the votes and comments of the old copy on the new one (checked on a live book page). From this milestone on, votes and comments are counted the same way, so some ratings here differ a little from `reports/fingerprints.md`.

## Part A. Data checks

### Reader comments

The table `reviews` has the columns `Name`, `Time`, `BookId`, `Text`: reader name, date and time, book number, text. 823,860 rows on 191,395 different books; 0 rows have an empty text and are ignored. **Comments have dates** (column `Time`), so the newest comments can be shown first.

| Pile | Books | With comments | Share | Comments per book: lower quarter / median / upper quarter | Same, only books that have comments |
| --- | --- | --- | --- | --- | --- |
| Liked books (all in the library) | 309 | 301 | 97.4% | 6 / 13 / 30 | 7 / 14 / 30 |
| Liked books used in the models | 294 | 289 | 98.3% | 7 / 13 / 30 | 7 / 14 / 30 |
| Rated-matched set (all) | 1044 | 954 | 91.4% | 2 / 5 / 12 | 2 / 5 / 13 |
| Rated-matched set, eligible only | 509 | 476 | 93.5% | 3 / 5 / 14 | 3 / 6 / 15 |
| Catalog fiction with at least 5 votes | 84684 | 71623 | 84.6% | 1 / 3 / 8 | 2 / 4 / 9 |

So the typical liked book has 13 comments and the typical rated-matched book has 5. My liked books are commented more, the same way they have more votes.

### Series of my liked books

For every book the catalog index stores the series name, the number in the series, and how many books the series has in the catalog (story series by the author only; publisher series are ignored). A book is:

- **standalone**: not in a series
- **first**: number 1 of its series
- **continuation**: a later book of a series where I already have a liked book with a lower number
- **unknown**: in a series, but the catalog gives no number (eligible, but flagged)
- **later**: a later book of a series where I have no earlier liked book (not eligible)

|  | Standalone | First in a series | Later, with an earlier liked book (continuation) | Later, without an earlier liked book | In a series, number unknown | Total |
| --- | --- | --- | --- | --- | --- | --- |
| All liked books | 87 | 108 | 56 | 26 | 32 | 309 |
| share | 28.2% | 35.0% | 18.1% | 8.4% | 10.4% |  |
| Liked books used in the models (no collections, no non-fiction) | 82 | 108 | 56 | 26 | 22 | 294 |

Of the 82 liked books that are a later book of a series, **56 have an earlier book of the same series among my liked books and 26 do not.**

The liked books that break the rule (a later book, no earlier book of the series in my library):

| Author | Title | Series | Number | Books in the series |
| --- | --- | --- | --- | --- |
| Марк Коултер | Никогда не ошибается | 99 мимолётных фантазий | 11 | 19 |
| Гоблин MeXXanik, Каин | Чехов. Книга 8. Темный всадник [СИ] | Адвокат Чехов | 8 | 15 |
| Татьяна Серганова | Ведьма по наследству | Ведьмы [Серганова] | 2 | 2 |
| Галина Герасимова | Ищи на улице Фей. Книга 2 | Винтер Крипс | 2 | 2 |
| Александра Лисина | 12. След мастера | Гибрид | 12 | 17 |
| Екатерина Началова | Загадки Лисы | Дочь Скорпиона | 3 | 12 |
| Аня Истомина | Как приручить полковника | Как приручить... (можно читать отдельно) | 2 | 5 |
| Гэлбрейт Роберт | Неизбежная могила | Корморан Страйк | 7 | 8 |
| Софья Валерьевна Ролдугина | Кофе и полынь | Кофейные истории | 16 | 16 |
| Инди Видум | Ступень Пятая | Крылья Мальгуса | 8 | 8 |
| Евгений Юрьевич Лукин | Портрет кудесника в юности | Лыцк, Баклужино, Суслов | 3 | 27 |
| Макс Далин | Костер и Саламандра. Книга 3 | Мир Королей | 6 | 10 |
| Лесса Каури | Медиум идет по следу | Норрофинд | 2 | 6 |
| Марина Владимировна Ефиминюк | Давай поспорим | Они на нас поспорили! | 2 | 5 |
| Гриша Гремлинов, Сергей Шиленко | Магазин волшебных питомцев - 5 | ПЭТЫ | 5 | 8 |
| Нинель Мягкова | Магия большого города. Журналистка | Прогресс и магия | 2 | 4 |
| Елена Ивановна Михалкова | Колодец и бабочка | Расследования Макара Илюшина и Сергея Бабкина | 32 | 35 |
| Роберт Шекли | Запах мысли | Сборник рассказов «Дипломатическая неприкосновенность» | 3 | 11 |
| Сергей Мусаниф | Борден | Система дефрагментации | 7 | 9 |
| Алайна Салах | Только между нами | Скандал [Салах] | 5 | 8 |
| Марианна Красовская, Нани Кроноцкая | Некромантика по любви | Туманная Галлия | 2 | 2 |
| Котаро Исака | Поезд убийц | Убийца | 2 | 5 |
| Данил Колосов | Родная кровь | Химера [Колосов] | 2 | 3 |
| Самюэль Бьорк | Хитра | Холгер Мунк и Миа Крюгер | 4 | 5 |
| М. С. Битон | Смерть негодяя | Хэмиш Макбет | 2 | 4 |
| Кирилл Коваль | Шеф-повар придорожной таверны IV | Шеф повар | 4 | 4 |

Some of these are loose series where every book is a separate story (detective series, shared worlds), and for some I may have read the earlier books elsewhere. The library cannot tell these apart.

### Series length: liked books against the rated-matched set

| Pile | Books | In a series | Books in the series: lower quarter / median / upper quarter | Series longer than 5 books | Series longer than 10 books | A later book of its series |
| --- | --- | --- | --- | --- | --- | --- |
| Liked (used in the models) | 294 | 72.1% | 3 / 3 / 8 | 32.1% | 16.0% | 27.9% |
| Rated-matched set (all) | 1044 | 76.3% | 3 / 5 / 10 | 47.3% | 24.8% | 54.3% |
| Liked: first in a series only | 108 | 100.0% | 2 / 3 / 5 | 21.3% | 11.1% | 0.0% |
| Rated-matched: first in a series only | 183 | 100.0% | 2 / 3 / 5 | 19.7% | 3.8% | 0.0% |

The two "first in a series only" rows are the fair comparison for "am I suspicious of long series?": both sides are number 1 of their series. There the median series has 3 books for my liked books and 3 for the rated-matched set; series longer than 5 books: 21.3% against 19.7%. The rated-matched set was drawn without looking at series, and 51.2% of it are later books of series I never started. Under my rule those were never real candidates, which is why they are left out from now on.

## The rules, applied to the whole catalog

How many catalog books pass each rule in turn. Different editions of one book are counted separately here.

| Rule | Books left |
| --- | --- |
| Russian-language books in the catalog, not deleted | 609,096 |
| ... that are fiction | 425,639 |
| ... and not a collection (by title or size) | 422,957 |
| ... and not already in my library | 422,631 |
| ... with at least 5 votes (the rating can be trusted) | 84,384 |
| ... with an average of 3 or more | 49,280 |
| ... eligible under the series rule | 29,043 |

The series rule, on the 49,280 books that pass every rule before it: 17,567 standalone, 8,474 first, 314 continuation, 2,688 unknown, 20,237 later. So the rule removes 20,237 books (41.1%). Of the books that pass everything, 10,808 have an average above 4 (the ones I prefer).

### The same rules on the downloaded books

| Set | Books | At least 5 votes and average 3 or more | ... and eligible | ... of those: standalone / first / continuation / number unknown |
| --- | --- | --- | --- | --- |
| Rated-matched | 1044 | 1021 | 502 | 245 / 179 / 32 / 46 |
| Old matched (not in rated-matched) | 1055 | 77 | 42 | 23 / 16 / 0 / 3 |
| Feed | 314 | 8 | 2 | 0 / 2 / 0 / 0 |
| All downloaded books | 2413 | 1106 | 546 | 268 / 197 / 32 / 49 |

Only eligible books are used in the comparison set, in the lists and in the labelling queue.

### My old answers on books that are not eligible

Of my 100 answers, **52 are on books my series rule would never have shown me** (31 would never read, 19 maybe, 2 want to read). They are marked "rule" and left out of every taste metric. 48 answers remain (27 would never read, 18 maybe, 3 want to read).

## Part D. Signals for Decision 1

**The main test.** Positive = my liked books that are standalone or first in a series (190 books). Comparison = rated-matched books that are standalone or first in a series (430 books). Continuations and books with an unknown number are left out on both sides: by my rule a continuation is decided by the earlier book, not by these signals, and an unknown number would give the model a shortcut. Same test as before: author-grouped, 5 folds, 10 repeats (50 test folds); an author is never on both sides; every scale, PCA direction, penalty strength, list of like-minded readers and set of nearest liked books comes from the training part only.

The signals:

- **rb**: average rating, number of votes, genre groups, length, translated, in a series (the model from milestone 3b)
- **series length**: the number of books in the series (log), 0 for a standalone book. "First in a series or standalone" is already the rb flag "in a series", because the main test only has standalone books and first books. "Continuation" cannot be a feature here: all continuations are left out, see below.
- **like-minded readers**: readers who gave 4 or 5 to at least 3 of my liked books: how many of them voted for the book, their share, and how much higher they rated it
- **item-to-item**: every reader's marks are centred on that reader's own average; two books are similar when the same readers rate both above (or both below) their average (cosine similarity). The score of a book is its similarity to the 5 nearest liked books (average, and the best one). Only liked books of the training part are used, and never books by the same author
- **reader comments**: the number of comments (log), and the embedding of the comment texts (same model as the books, bge-m3; the newest 30 comments of a book, averaged; 20 PCA directions learned inside each training fold)

| Model | **ROC AUC** | Median percentile of a liked book | Liked books in the top 5% | AUC liked vs "would never read" |
| --- | --- | --- | --- | --- |
| rb: ratings + genres, length, translated, in a series | **0.764 ± 0.052** | 18.2% ± 6.7 | 20.5% ± 9.5 | 0.760 ± 0.018 |
| rb+s: rb + series length | **0.766 ± 0.050** | 18.1% ± 6.6 | 20.6% ± 8.6 | 0.771 ± 0.010 |
| rb+s+lm: + like-minded readers | **0.772 ± 0.052** | 16.1% ± 6.5 | 22.5% ± 12.0 | 0.763 ± 0.015 |
| rb+s+lm+i2i: + item-to-item | **0.771 ± 0.051** | 15.8% ± 5.7 | 22.7% ± 11.1 | 0.776 ± 0.012 |
| rb+s+lm+i2i+c: + reader comments | **0.780 ± 0.049** | 15.1% ± 6.4 | 25.3% ± 12.0 | 0.782 ± 0.015 |
| rb+s+i2i: item-to-item without like-minded readers | **0.768 ± 0.050** | 16.1% ± 7.0 | 24.0% ± 10.1 | 0.793 ± 0.009 |
| rb+s+lm+c: comments without item-to-item | **0.782 ± 0.049** | 15.6% ± 6.6 | 24.3% ± 11.9 | 0.774 ± 0.015 |

ROC AUC: 0.5 is coin-flipping, 1.0 is perfect. Median percentile: where the typical liked test book sits in the ranking of the comparison test books (lower is better, 50% is chance). Top 5%: the share of liked test books that score above 95% of the comparison books (chance gives 5%). The last column separates the liked books from the 27 eligible books I marked "would never read"; with so few books it is rough.

### Fold by fold: which additions are real?

Each row compares two models on the same 50 folds, which removes most of the fold-to-fold noise.

| Comparison | What it tests | AUC gain | Beats the noise? |
| --- | --- | --- | --- |
| rb+s against rb | series length on top of rb | +0.002 ± 0.011 (better in 29 of 50 folds) | no, inside the noise |
| rb+s+lm against rb+s | like-minded readers on top of that | +0.006 ± 0.014 (better in 34 of 50 folds) | no, inside the noise |
| rb+s+lm+i2i against rb+s+lm | item-to-item on top of that | -0.001 ± 0.011 (better in 29 of 50 folds) | no, inside the noise |
| rb+s+lm+i2i+c against rb+s+lm+i2i | reader comments on top of that | +0.009 ± 0.014 (better in 38 of 50 folds) | no, inside the noise |
| rb+s+i2i against rb+s | item-to-item instead of like-minded readers | +0.002 ± 0.015 (better in 31 of 50 folds) | no, inside the noise |
| rb+s+lm+i2i against rb+s+i2i | like-minded readers when item-to-item is already there | +0.003 ± 0.013 (better in 34 of 50 folds) | no, inside the noise |
| rb+s+lm+c against rb+s+lm | reader comments without item-to-item | +0.010 ± 0.014 (better in 38 of 50 folds) | no, inside the noise |

- Series length on top of rb: **does not beat the noise** (+0.002 ± 0.011 (better in 29 of 50 folds)).
- Like-minded readers on top of that: **does not beat the noise** (+0.006 ± 0.014 (better in 34 of 50 folds)).
- Item-to-item on top of that: **does not beat the noise** (-0.001 ± 0.011 (better in 29 of 50 folds)).
- Reader comments on top of that: **does not beat the noise** (+0.009 ± 0.014 (better in 38 of 50 folds)).

There are 973 like-minded readers on average inside a training part (from 867 to 1084).

### Author on my list (step 13)

**My list of followed authors is still empty** (tab "My authors" of the labelling page). This signal is stored for every book and used in the short list and in the test of Decision 1 below, but it is **not** in the table above, on purpose: the list is made from my liked books, so every book by a listed author in the test would be a liked one, and the test is author-grouped, so a model never sees the same author on both sides. Any number for it there would be meaningless. Its real test is my answers in the "New books" tab, reported separately for listed and unknown authors.

### Continuations, reported separately

56 of the liked books used in the models are continuations, and 32 rated-matched books are (unread later books of series I liked). They are in no model. For reference, the best model (rb+s+lm+c) gives the liked continuations a median score of 0.60, against 0.65 for the liked books of the main test and 0.32 for the comparison books; these scores come from models that never saw the author.

In the catalog, 460 fiction books are continuations of series I liked and are not in my library (editions counted separately). The next book of each series I have started (my highest liked number + 1), when the catalog has it:

| Rating | Votes | Id | Author | Title | Series |
| --- | --- | --- | --- | --- | --- |
| 4.56 | 66 | 140422 | Гарри Гаррисон | Зима в Эдеме | Эдем (2 of 4) |
| 4.56 | 43 | 868351 | Тата Алатова | Неисправная Анна. Книга 2 | Неисправная Анна (2 of 2) |
| 4.54 | 26 | 273255 | Артур Конан Дойль | Белый отряд | Сэр Найджел (2 of 2) |
| 4.48 | 432 | 302744 | Оксана Петровна Панкеева | О пользе проклятий | Судьба короля [= Хроники странного королевства] (2 of 13) |
| 4.38 | 34 | 862591 | Алексей Юрьевич Пехов | Птицелов | Птицы и солнцесветы (2 of 2) |
| 4.38 | 8 | 552678 | Джей Кристофф | Годсгрейв | Хроники Неночи (2 of 3) |
| 4.35 | 26 | 276698 | Элеонора Генриховна Раткевич | То, чего нет | Наёмник Мёртвых Богов (2 of 5) |
| 4.33 | 119 | 214730 | Робин Хобб | Странствия убийцы | Мир Элдерлингов (3 of 18) |
| 4.32 | 22 | 267458 | Александра Лисина | Холодные дни | Грани отражений (2 of 3) |
| 4.28 | 82 | 538907 | Домагой Курмаич | В большом мире | Мать Ученья (2 of 3) |
| 4.25 | 16 | 886033 | Илона Эндрюс | На страже мира | Хроники хозяйки отеля (2 of 5) |
| 4.25 | 92 | 619160 | Павел Сергеевич Иевлев | Мертвая женщина играет на скрипке | Время кобольда (2 of 3) |
| 4.25 | 20 | 334898 | Фред Варгас | Человек наизнанку | Комиссар Адамберг (2 of 10) |
| 4.22 | 32 | 857268 | Екатерина Насута | Не судьба | Некроманты и все-все-все (3 of 4) |
| 4.20 | 5 | 708482 | Василиса Усова | Эйрика. Путешествие за горизонт | Мачеха для Золушки (2 of 2) |
| 4.20 | 46 | 155616 | Джоанн Харрис | Леденцовые туфельки | Шоколад (2 of 4) |
| 4.19 | 36 | 391321 | Александра Плен | Половинка моего сердца | Магические дары (2 of 2) |
| 4.19 | 36 | 457263 | Ирина Владимировна Котова | Скрытое пламя | Королевская кровь [Котова] (2 of 15) |
| 4.18 | 39 | 703535 | Екатерина Насута | Драконья кровь (litres) | Драконий берег (2 of 2) |
| 4.18 | 17 | 815967 | Юлия Викторовна Журавлева | Хранительница чудовищ (СИ) | Целители магических животных (6 of 8) |
| 4.17 | 113 | 577074 | Марина Суржевская | Королевство Бездуш. Lastfata | Королевство Бездуш (2 of 2) |
| 4.16 | 19 | 353715 | Патрик О'Брайан | Капитан первого ранга | Хозяин морей (2 of 20) |
| 4.16 | 127 | 569097 | Борис Вячеславович Конофальский | Мощи святого Леопольда | Путь инквизитора [= Инквизитор] (2 of 18) |
| 4.16 | 32 | 862493 | Борис Вячеславович Конофальский | Охотник | Во сне и наяву [=Дорогой снов] (2 of 3) |
| 4.15 | 13 | 888405 | Алекс Кош | Тени Грейфолла 2 | де Грейв (2 of 3) |

(73 more are not shown.)

## Part E. Text test for Decision 2

Every book now has a second fingerprint made from its opening only: the first 25,000 words (about 100 pages), with the same stylometry numbers and the same embedding method as the whole book (the same number of chunks, spread through the opening). 2038 books have no opening vectors yet (run `npm run funnel` again). Opening and whole-book vectors of the same book have a cosine similarity of 0.981 on average.

> **Skipped: 0 books are marked "read it, did not like it" and have text; the test needs at least 30.** 1 more are marked but not downloaded yet (run `npm run funnel`). Mark books in the tabs "Books by my authors" (key 1) and "Add a book" of the labelling page, then run `npm run funnel` again: it downloads them politely, fingerprints them and runs this test.

What the test will do: liked books against books I read and did not like, with liked books by the same authors where there are enough of them; four models (ratings and genres only; whole-book text only; opening text only; ratings and genres plus the better text version); author-grouped folds, so no book and no author is in both the training part and the test part.

## The short list

### By my authors

**My list of authors is still empty, so this uses every author with at least 2 liked books** (55 authors). Fill the list in the tab "My authors" to make it mine. Eligible fiction books by these authors that are not in my library and not marked as read, newest first. One edition per title; books with at least 5 votes and an average below 3 are left out. 1268 books in all, the newest 40 are shown.

| Year | Rating | Votes | Id | Author | Title | Series |
| --- | --- | --- | --- | --- | --- | --- |
| 2026 | 2.33 | 3 | 895502 | Инди Видум | Осторожно, магия открывается! Том 1 | Осторожно, магия открывается! (1 of 1) |
| 2026 |  | 0 | 895307 | Софья Валерьевна Ролдугина | Осень со вкусом кофе. Коллекция уютных историй |  |
| 2026 | 3.96 | 25 | 894502 | Борис Вячеславович Конофальский | Божьим промыслом. Засовы и решётки | Путь инквизитора [= Инквизитор] (18 of 18) |
| 2026 | 3.27 | 11 | 894178 | Сева Сотх | Ретро Бит. Акт 10 | Ретро Бит (10 of 10) |
| 2026 | 4.11 | 9 | 893993 | Ольга Александровна Куно | Прочнее оков |  |
| 2026 | 3.20 | 5 | 893845 | Нинель Мягкова | Последняя Гадина империи | Яды и тотемы (1 of 1) |
| 2026 | 3.56 | 27 | 893679 | Сергей Сергеевич Мусаниф | Кодекс физрука | Кодекс физрука (1 of 1) |
| 2026 | 3.67 | 3 | 892640 | Анюта Соколова | Поводок |  |
| 2026 |  | 0 | 892634 | Софья Валерьевна Ролдугина | Рыжий и скрипка | Игры Эн Ро Гримм (1 of 1) |
| 2026 | 3.24 | 38 | 892226 | Инди Видум | Бездна | Петя и Валерон (11 of 11) |
| 2026 | 5.00 | 1 | 892165 | Кирилл Сергеевич Клеванский | Матабар XI | Матабар (11 of 11) |
| 2026 | 1.00 | 1 | 891764 | Максим Андреевич Далин | Время неблагих. Книга 3 | Мир Королей (10 of 10) |
| 2026 | 3.36 | 11 | 891631 | Сева Сотх | Ретро Бит. Акт 9 | Ретро Бит (9 of 10) |
| 2026 |  | 0 | 891376 | Брендон Сандерсон | Огни Декабрины | Космер. Мирал (1 of 1) |
| 2026 | 3.93 | 15 | 891339 | Екатерина Насута | Кицхен вступает в бой | Такие странные Каэр (3 of 3) |
| 2026 | 3.00 | 1 | 891076 | Екатерина Насута | Кровь зимы | Бастионы (1 of 1) |
| 2026 |  | 0 | 890299 | Анна Шнайдер | Закрыть гештальт | Семейные ценности (number not known) |
| 2026 |  | 0 | 890298 | Анна Шнайдер | Предатели тоже плачут | Семейные ценности (number not known) |
| 2026 | 3.33 | 6 | 889935 | Анюта Соколова | Эмкаэлка |  |
| 2026 | 3.00 | 2 | 889932 | Анюта Соколова | Диагноз |  |
| 2026 | 4.33 | 3 | 889267 | Елена Кароль | Хорошая, плохая, грешная 1 | Дикий Дикий Запад (1 of 3) |
| 2026 | 1.00 | 1 | 888900 | Елена Кароль | Княжьи постебушки |  |
| 2026 |  | 0 | 888836 | Дарья Верескова | Хранители Тории: Город запретных желаний | Из Приграничья в Изнанку (1 of 1) |
| 2026 | 3.00 | 3 | 888801 | Елена Кароль | Химера растительного происхождения. Часть 1 | Химера (1 of 1) |
| 2026 | 1.00 | 2 | 888603 | Анна Ледова | Тарья берёт своё | Вогульская княжна (1 of 1) |
| 2026 | 4.00 | 4 | 888299 | Юлия Стешенко | Сага о принце на белом коне. Книга 2 | Темные сказки белого севера (3 of 4) |
| 2026 | 3.40 | 5 | 888068 | Дарья Верескова | Подарок для рыцаря смерти |  |
| 2026 | 3.73 | 11 | 888010 | Марина Ефиминюк | Драконье счастье | Драконовы невесты (3 of 3) |
| 2026 | 3.71 | 45 | 887576 | Инди Видум | Рубеж | Петя и Валерон (10 of 11) |
| 2026 | 3.17 | 6 | 887250 | Александра Лисина | Ловушка для мастера | Гибрид (17 of 17) |
| 2026 | 3.33 | 9 | 885972 | Елена Кароль | Инженер с того света |  |
| 2026 | 4.00 | 3 | 884994 | Татьяна Владимировна Солодкова | Прыгать с платформы запрещается |  |
| 2026 | 3.65 | 31 | 884789 | Сева Сотх | Ретро Бит. Акт 7 | Ретро Бит (7 of 10) |
| 2026 | 2.00 | 2 | 884716 | Наталья Сергеевна Филимонова | Нет предела сове… |  |
| 2026 | 3.68 | 31 | 883868 | Екатерина Насута | Кицхен готовится к войне | Такие странные Каэр (2 of 3) |
| 2026 | 2.00 | 1 | 883738 | Анна Шнайдер | Я тебя не предам |  |
| 2026 | 3.62 | 34 | 883585 | Павел Сергеевич Иевлев | Беги за мёртвым кроликом |  |
| 2026 | 3.71 | 21 | 883445 | Роман Г. Артемьев | Война | Песня штормов (3 of 3) |
| 2026 | 2.67 | 3 | 883166 | Кирилл Сергеевич Клеванский | Матабар X | Матабар (10 of 11) |
| 2026 | 5.00 | 1 | 881573 | Милена Валерьевна Завойчинская | Отпуск. Диета. Ведьмак на подхвате | Хочу в отпуск (number not known) |

### New for me

The 20 best eligible books by other authors that I have not labelled, by the best model (**rb+s+lm+c**). Candidates: downloaded books with at least 5 votes and an average of 3 or more (463 books). Each score comes from models that never saw the book or its author.

| Score | Rating | Votes | Comments | Key | Author | Title | Series | Genre groups |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.89 | 4.32 | 47 | 22 | 712101 | Маргарита Александровна Гришаева | Приворот на василиска |  | romantic fantasy, contemporary romance |
| 0.87 | 4.18 | 99 | 53 | 575529 | Кира Алиевна Измайлова | Старое платье королевы |  | romantic fantasy |
| 0.86 | 3.72 | 240 | 152 | 620295 | Дмитрий Олегович Смекалин | Господин маг | Господин маг (1 of 3) | fantasy |
| 0.86 | 3.97 | 151 | 98 | 750889 | Александр Н. Александров | Следователь | Фигаро, следователь Департамента Других Дел (1 of 8) | detective, fantasy, science fiction, web literature |
| 0.85 | 3.66 | 64 | 28 | 807324 | Полина Ром | Усадьба леди Анны |  | romantic fantasy, web literature, fantasy |
| 0.84 | 4.03 | 59 | 26 | 619223 | Яна Лисканова | План по соблазнению герцога (СИ) | Записки милых дам (1 of 2) | romantic fantasy, humour, web literature |
| 0.83 | 3.75 | 454 | 225 | 296597 | Вадим Владимирович Денисов | Замок Россия | Стратегия [Денисов] (1 of 11) | science fiction |
| 0.83 | 3.83 | 46 | 20 | 772979 | Джейд Дэвлин | Микрофон на цепи | Попаданка и ее айдолы (1 of 5) | web literature, popadantsy, fantasy, romantic fantasy |
| 0.82 | 3.66 | 86 | 34 | 612428 | Оксана Чекменёва | Целительница моей души |  | romantic fantasy, web literature, fantasy |
| 0.82 | 4.17 | 113 | 33 | 577074 | Марина Суржевская | Королевство Бездуш. Lastfata | Королевство Бездуш (2 of 2) | romantic fantasy |
| 0.81 | 4.32 | 340 | 68 | 150905 | Ольга Громыко | Год Крысы. Видунья | Год Крысы (1 of 2) | fantasy |
| 0.80 | 4.14 | 73 | 31 | 789434 | Яна Ясная | Секс и ничего личного |  | romantic fantasy, erotica, web literature |
| 0.80 | 4.19 | 153 | 77 | 531107 | Артем Каменистый | Существование | Трэш (1 of 2) | science fiction, fantasy, popadantsy |
| 0.79 | 3.88 | 65 | 17 | 687368 | Марианна Красовская | Дух воина | Люди огня (1 of 4) | romantic fantasy |
| 0.79 | 3.98 | 175 | 34 | 348068 | Елена Звездная | Дочь воина, или Кадеты не сдаются | Космос, юмор и немного любви (number not known) | romantic fantasy |
| 0.78 | 4.00 | 48 | 24 | 664751 | Алиса Чернышова | Объект номер 13 | Галактика Альдазар (1 of 3) | romantic fantasy, web literature |
| 0.78 | 3.81 | 36 | 13 | 805163 | Яна Ясная | Гадости для радости ...и по работе | Иные соседи (межавторский цикл) (number not known) | romantic fantasy, web literature |
| 0.78 | 4.14 | 525 | 85 | 156339 | Алекс Кош | Огненный Факультет | Ремесло (1 of 4) | fantasy |
| 0.78 | 4.54 | 69 | 36 | 408244 | Джордж Оруэлл | Скотный Двор |  | classic prose, contemporary prose, science fiction |
| 0.77 | 4.37 | 338 | 50 | 352100 | Руслан Алексеевич Михайлов | Ярость богов | Мир Вальдиры (number not known) | litrpg |

## The real test of Decision 1: my answers in the "New books" tab

The queue of the tab is rebuilt by every run of this report: 100 books the best model scores highest and 100 books picked at random from the 498 eligible candidates, mixed together; the page does not show which is which, and every answer stores the half the book came from. Average score: 0.68 in the top half, 0.38 in the random half.

> **Skipped: 0 new answers so far; the comparison needs at least 60.** Answer books with `npm run label`, then run `npm run funnel` again. The earlier answers were given before the queue had two halves, so they cannot be used here.
