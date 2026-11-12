# Text fingerprints

Built by `model/fingerprints.py` (run everything with `npm run fingerprints`). Milestone 2 scored a book by simple facts such as genre and length. This report asks whether the *text itself* helps: how the book is written (stylometry) and what it is about (embeddings).

## Books used

| Group | Books |
| --- | --- |
| Liked books in the archive | 309 |
| ... with FB2 text downloaded from the mirror | 309 (100.0%) |
| ... still on EPUB text (FB2 could not be downloaded) | 0 |
| ... left out as collections | 11 |
| ... left out as non-fiction | 4 |
| **Liked books used (positive class)** | **294** |
| Matched background: books picked from the catalog | 1150 |
| ... downloaded and parsed | 1150 |
| ... download or parse failed | 0 |
| ... not tried yet | 0 |
| **Matched books used (unlabeled, source = matched)** | **1150** |
| **Feed books used (unlabeled, source = feed)** | **314** |
| Different author groups (co-authors count as one group) | 1394 |

The matched background is a random sample of Russian-language fiction from the catalog (not liked, not deleted, not a collection, at least 20,000 words when the catalog knows the size), drawn so that its years follow the years of the liked books. Genre was not matched. A parsed matched book is dropped when it turns out to be a collection, a duplicate, or not Russian-language.

| Pile | Year: lower quarter / median / upper quarter |
| --- | --- |
| Liked | 2016 / 2022 / 2024 |
| Matched | 2016 / 2022 / 2024 |
| Feed | 2026 / 2026 / 2026 |

### Download failures

Every liked book has FB2 text.

Matched books: no failures in 1150 downloads.

## Ratings in the catalog

The catalog dump has reader ratings from 1 to 5. They are **not used in any model yet**; this is only a count.

| Pile | Books | Books with a rating | Share | Average rating | Median number of votes |
| --- | --- | --- | --- | --- | --- |
| Liked | 294 | 293 | 99.7% | 3.97 | 28 |
| Unlabeled (all) | 1464 | 913 | 62.4% | 2.60 | 3 |
| ... matched | 1150 | 810 | 70.4% | 2.70 | 3 |
| ... feed | 314 | 103 | 32.8% | 1.87 | 2 |

"Average rating" is the average over books of each book's own average. Feed books are a few days old, so few readers have rated them yet.

## Honest checks on the two piles

### Check 1: can the text tell a feed book from a matched book?

A model was trained to separate the 314 feed books from the 1150 matched books, using only the embeddings (author-grouped, 5 folds, 10 repeats). **ROC AUC 0.717 ± 0.048.** 0.5 would mean the two piles look the same.

This is not above 0.75, so the upload week is not strongly visible in the text. The feed and the matched background can be mixed.

### Check 2: does the file format change the embedding?

For 30 liked books the EPUB text and the FB2 text of the same book were embedded separately. **Average cosine similarity 1.0000** (lowest 0.9998). For comparison, two *different* liked books have 0.843 on average. 1.0 means identical.

So the format barely moves a book's vector compared with the distance between books.

## How well the models rank

Positive = liked books. Unlabeled = matched + feed. No model uses the year. All are logistic regressions:

- **b0** - genres + length, translated, series
- **s** - b0 + stylometry
- **e** - embeddings only
- **be** - b0 + embeddings
- **all** - b0 + stylometry + embeddings

Embeddings: 50 PCA directions, learned inside each training fold. Test: author-grouped, 5 folds, 10 repeats (50 test folds); an author is never on both sides. Every scale and the model's penalty strength are also chosen inside the training fold.

| Model | ROC AUC | Median percentile of a liked book | Liked books in the top 5% | AUC gain over b0 (same folds) |
| --- | --- | --- | --- | --- |
| b0: genres + length, translated, series | 0.718 ± 0.040 | 18.6% ± 5.4 | 16.6% ± 6.8 |  |
| s: b0 + stylometry | 0.770 ± 0.038 | 15.1% ± 5.6 | 24.2% ± 8.3 | +0.052 ± 0.027 (better in 49 of 50 folds) |
| e: embeddings only | 0.769 ± 0.041 | 15.5% ± 5.4 | 26.3% ± 7.7 | +0.051 ± 0.039 (better in 44 of 50 folds) |
| be: b0 + embeddings | 0.780 ± 0.039 | 14.0% ± 4.7 | 28.0% ± 8.6 | +0.062 ± 0.033 (better in 50 of 50 folds) |
| all: b0 + stylometry + embeddings | 0.799 ± 0.037 | 11.9% ± 4.6 | 32.4% ± 10.0 | +0.080 ± 0.034 (better in 50 of 50 folds) |

How to read the numbers:

- **ROC AUC**: 0.5 is coin-flipping, 1.0 is a perfect ranking. The number after ± is how much it moves from fold to fold.
- **Median percentile**: each liked test book is placed in the ranking of the unlabeled test books. 10% means the typical liked book sits in the top tenth of the pile; 50% would be pure chance. Lower is better.
- **Top 5%**: the share of liked test books that score higher than 95% of the unlabeled test books. Chance gives 5%. This is the realistic one: it is close to "if I only look at the best 1 in 20 new books, how many of my books would I find".
- **AUC gain over b0**: the difference to model b0 measured fold by fold on the same folds, which removes most of the fold-to-fold noise.

### Does text beat 0.79?

**Not clearly.** The best text model (all) reaches 0.799, which is +0.009 against 0.79, but the fold-to-fold noise is ±0.037. The gain is inside the noise.

A caution about that comparison: 0.79 was measured against feed books only and with the year as a feature; today's test uses a different, fairer unlabeled pile. The like-for-like comparison is against **b0 on the same books and folds**: b0 scores 0.718 ± 0.040, model all scores 0.799, a gain of +0.080 ± 0.034 per fold, and all wins in 50 of 50 folds. That gain is larger than its own fold-to-fold spread, so it is real.

Where it comes from: stylometry added to b0 gains +0.052 ± 0.027; embeddings alone, with no genre at all, differ from b0 by +0.051 ± 0.039.

### Does the model transfer to new uploads?

Here the test is always "liked test books against **feed** test books". The first row is model be as above; the second was trained without seeing a single feed book (unlabeled = matched only).

| Model be, trained with | ROC AUC on liked vs feed | Median percentile | Top 5% |
| --- | --- | --- | --- |
| matched + feed as unlabeled | 0.783 ± 0.050 | 15.0% ± 6.1 | 26.5% ± 12.0 |
| matched only as unlabeled | 0.735 ± 0.058 | 20.4% ± 6.9 | 16.5% ± 11.1 |

Training without feed books changes the AUC on new uploads by -0.048. Part of the first row's score comes from recognising the feed pile itself; the second row is the honest estimate for new uploads.

## My answers from the labelling page

Answers so far: **0** (0 would never read, 0 maybe, 0 want to read). Starred favourites: 0.

> **Fewer than 100 answers, so the label step is skipped**: no "liked vs would never read" metric, no all+labels model, and nothing is removed from the top-30 list. Start the page with `node src/label-server.js`, answer some more, and run the report again.

### Genre groups: "would never read" against liked

No "would never read" answers yet, so this table is empty.

## Taste map: clusters of the liked books

The liked books were grouped by their embeddings (k-means on the book vectors, after taking away what all liked books have in common). The number of clusters was chosen by the silhouette score (higher = cleaner groups; values near 0 mean the groups overlap a lot).

| Clusters | Silhouette | Cluster sizes |  |
| --- | --- | --- | --- |
| 4 | 0.093 | 103, 73, 61, 57 | **chosen** |
| 5 | 0.086 | 82, 65, 59, 53, 35 |  |
| 6 | 0.080 | 67, 54, 50, 45, 44, 34 |  |
| 7 | 0.076 | 50, 48, 47, 42, 41, 34, 32 |  |
| 8 | 0.077 | 44, 43, 43, 38, 37, 33, 31, 25 |  |
| 9 | 0.067 | 44, 43, 38, 33, 30, 29, 28, 26, 23 |  |
| 10 | 0.067 | 44, 39, 34, 31, 31, 29, 27, 21, 21, 17 |  |

### Cluster 1: 103 books

- Most common authors: Анюта Соколова (8), Милена Валерьевна Завойчинская (5), Марина Владимировна Ефиминюк (4), Юлия Журавлева (4), Рада Мэй (4)
- Most common genre groups: romantic fantasy (87.4%), web literature (53.4%), fantasy (17.5%), detective (16.5%), humour (11.7%)
- Closest to the centre:

| Author | Title |
| --- | --- |
| Мария Морозова | Связанные целью |
| Ольга Куно | Шпионка в графском замке |
| Александра Лисина | Ведьма, тень и черный кот |
| Юлия Журавлева | Кафедра зооцелительства |
| Ольга Дмитриева | Обрученная с врагом (СИ) |
| Рада Мэй | Душа по обмену |
| Ольга Александровна Куно | В полушаге от любви |
| Милена Валерьевна Завойчинская | Струны волшебства. Книга вторая. Цветная музыка сидхе |

### Cluster 2: 73 books

- Most common authors: Карина Демина (3), Екатерина Владимировна Казакова (3), Кирилл Клеванский (3), Анджей Сапковский (2), Алёна Харитонова (2)
- Most common genre groups: fantasy (52.1%), science fiction (28.8%), web literature (27.4%), romantic fantasy (23.3%), popadantsy (11.0%)
- Closest to the centre:

| Author | Title |
| --- | --- |
| Алёна Харитонова, Екатерина Владимировна Казакова | Пленники раздора |
| Алёна Артёмовна Харитонова, Екатерина Владимировна Казакова | Наследники Скорби |
| Кристофер Бьюлман | Вор с черным языком |
| Анджей Сапковский | Последнее желание |
| Сергей Вацлавович Малицкий | Пагуба |
| Гарри Гаррисон | К западу от Эдема |
| Юрий Александрович Никитин | Трое из Леса. Трилогия |
| Алёна Харитонова, Екатерина Владимировна Казакова | Жнецы Страданий |

### Cluster 3: 61 books

- Most common authors: Павел Иевлев (4), Сева Сотх (4), Фредрик Бакман (3), Сергей Сергеевич Мусаниф (3), Сергей Мусаниф (2)
- Most common genre groups: web literature (26.2%), detective (26.2%), science fiction (23.0%), contemporary prose (21.3%), thriller (11.5%)
- Closest to the centre:

| Author | Title |
| --- | --- |
| Зиновий Юрьевич Юрьев | Финансист на четвереньках |
| Сева Сотх | Ретро Бит [8] |
| Сева Сотх | Ретро бит [5] |
| Сева Сотх | Ретро Бит [2] |
| Сева Сотх | Ретро Бит |
| Дин Кунц | Славный парень |
| Майк У. Крэйвен | Шоу марионеток |
| Роберт Гэлбрейт | Человек с клеймом |

### Cluster 4: 57 books

- Most common authors: Карина Демина (6), Анатолий Брусникин (2), Борис Конофальский (2), Сергей Мусаниф (2), Инди Видум (2)
- Most common genre groups: web literature (47.4%), fantasy (28.1%), romantic fantasy (19.3%), science fiction (15.8%), contemporary prose (12.3%)
- Closest to the centre:

| Author | Title |
| --- | --- |
| Елена Ивановна Михалкова | Колодец и бабочка |
| Карина Демина | Эльфийский бык |
| Карина Демина | Ведьмы.Ру |
| Андрей Александрович Васильев | Слеза Рода |
| Владимир Осипович Богомолов | Момент истины (В августе сорок четвертого...) |
| Борис Конофальский | Сталин против рептилоидов |
| Владимир Алексеевич Ильин | Напряжение |
| Гоблин MeXXanik, Каин | Чехов. Книга 8. Темный всадник [СИ] |

## The 30 unlabeled books with the highest score

Scores come from the model with the best ROC AUC, **all**, in the author-grouped test: every book is scored only by models that never saw the book or its author, averaged over the repeats. The score is between 0 and 1.

| Score | Key | Author | Title | Genres | Source |
| --- | --- | --- | --- | --- | --- |
| 0.92 | 739673 | Катерина Снежинская | Чуть больше мира | love_sf, network_literature | matched |
| 0.91 | 278084 | Ева Геннадьевна Никольская | Мой огненный и снежный зверь | love_sf, sf_humor | matched |
| 0.90 | 654888 | Ирина Владимировна Смирнова, Ива Лебедева | Дубина для Золушки | love_sf | matched |
| 0.89 | 647675 | Анна Викторовна Дашевская | Ущелье Самарья, в следующий вторник | network_literature, love_sf | matched |
| 0.89 | 618767 | Ольга Ивановна Коротаева | Моя очаровательная экономка | love_sf, network_literature | matched |
| 0.89 | 895378 | Надежда Цыбанова | Ремонт, магия и прочие неприятности | love_sf, detective, humor_prose | feed |
| 0.88 | 894687 | Галина Дмитриевна Гончарова | Замок над морем | sf_heroic, love_sf | matched |
| 0.88 | 254809 | Василий Горъ | Проклятие короля | sf_fantasy | matched |
| 0.88 | 804237 | Галина Васильевна Герасимова | Не пугай лису костями | love_sf, network_literature | matched |
| 0.87 | 534396 | Ника Ёрш | Замуж за варвара, или Монашка на выданье | love_sf | matched |
| 0.85 | 378791 | Александра Черчень | Дипломная работа по обитателям болота | love_sf | matched |
| 0.85 | 612113 | Бронислава Антоновна Вонсович | Вы все жжете! Часть 2 | network_literature, love_sf | matched |
| 0.85 | 348167 | Терри Пратчетт | Интересные времена | sf_humor | matched |
| 0.85 | 496182 | Наталья Самсонова | Жена по жребию | love_sf, network_literature | matched |
| 0.84 | 894027 | Станислав Кемпф, Андрей Ткачев | Возрождение Камелота. Том 1 | network_literature, boyar_anime, sf_fantasy_city | matched |
| 0.84 | 793831 | Елена Владимировна Крыжановская | Счастливчик Финт. Дом серых роз | adv_maritime, sf_humor, network_literature, unfinished | matched |
| 0.84 | 895281 | Елена Свободная | Белая целительница: вас много, а я одна! | love_sf, sf_humor | feed |
| 0.83 | 757206 | Михаил Рагимов, Игорь Игоревич Николаев | Плохие люди | sf_social, network_literature | matched |
| 0.83 | 381704 | Софья Николаевна Непейвода | Первые шаги | network_literature, sf_etc | matched |
| 0.83 | 717347 | Исаак Ландауэр | СМДБВБИП | prose_contemporary | matched |
| 0.83 | 361210 | Константин Давидович Мзареулов | Капитан Багровой Тьмы | sf_action, sf | matched |
| 0.82 | 895464 | Харитон Байконурович Мамбурин | Пыль в доме [СИ] | sf_mystic, thriller, sf_social, network_literature | feed |
| 0.82 | 125077 | Дмитрий Вересов, Феликс Разумовский | Сердце Льва | det_action | matched |
| 0.82 | 895358 | Ефимия Летова | Исцели меня | love_erotica, love_sf | feed |
| 0.82 | 837193 | Анна Солейн | Развод с драконом. Служанка в заброшенной усадьбе | love_sf, network_literature, everyday_fantasy | matched |
| 0.82 | 679819 | Анна Калина | Прогулки по тонкому льду | love_sf, network_literature | matched |
| 0.81 | 740749 | Валерия Дашкевич | Лунная Мелодия | love_sf, network_literature | matched |
| 0.81 | 895256 | Мария Камардина | Тёмное сердце Белого озера. Книга 1 | network_literature, love_sf | feed |
| 0.80 | 723260 | Ева Витальевна Шилова | Чужие грехи | sf_detective, adventure | matched |
| 0.80 | 227451 | Дороти Ли Сэйерс | Под грузом улик | det_classic | matched |

## The 20 liked books with the lowest score

Liked books that the best model would have missed.

| Score | Key | Author | Title | Genres |
| --- | --- | --- | --- | --- |
| 0.06 | 50164 | Роберт Шекли | Запах мысли | sf |
| 0.07 | 881239 | Марк Коултер | Никогда не ошибается | sf_fantasy, fan_translation |
| 0.07 | 849511 | Аня Истомина | Как приручить полковника | love_contemporary |
| 0.10 | 253815 | Владимир Осипович Богомолов | Момент истины (В августе сорок четвертого...) | prose_military |
| 0.12 | 707752 | Сергей Сергеевич Мусаниф | Реквием по победителям | sf_action, sf_postapocalyptic, sf_space, network_literature |
| 0.13 | 634327 | Алайна Салах | Только между нами | love_contemporary, network_literature |
| 0.13 | 563756 | Сергей Сергеевич Мусаниф, Роман Валерьевич Злотников | Вселенная неудачников | sf_action, sf_space |
| 0.14 | 183933 | Сергей Мусаниф | О людях и бегемотах | sf_humor |
| 0.15 | 802226 | Ирина Шайлина | Лишняя женщина | love_contemporary, network_literature |
| 0.18 | 449763 | Влас Михайлович Дорошевич | Дело о людоедстве | prose_rus_classic, story |
| 0.18 | 816811 | Анна Владимирова | Мой бывший - зверь | love_sf, love_erotica, network_literature |
| 0.18 | 815679 | Эмма Хамм | Шепот глубин | love, love_sf |
| 0.21 | 264790 | Анатолий Брусникин | Беллона | prose_history, adv_maritime, prose_military |
| 0.21 | 273254 | Артур Игнатиус Конан Дойл | Сэр Найджел Лоринг | adv_history |
| 0.22 | 661398 | Себастьян Жапризо | Дама в автомобиле, с ружьем и в очках | detective |
| 0.22 | 505502 | Оливье Бурдо | В ожидании Божанглза | prose_contemporary |
| 0.23 | 780146 | Алексей Борисович Сальников | Когната | sf_fantasy |
| 0.23 | 748639 | Тосикадзу Кавагути | Пока не остыл кофе | prose_contemporary |
| 0.24 | 328899 | Василий М. Маханенко | Барлиона | sf_litrpg, popadancy |
| 0.25 | 179235 | Джон Паркс Лукас Бейнон Харрис Уиндем | Куколки | sf, dystopian |

## Appendix: stylometry averages

The numbers measured on each book's full text, averaged per pile.

| Measure | Liked | Matched | Feed |
| --- | --- | --- | --- |
| average sentence length (words) | 8.91 | 9.52 | 8.87 |
| spread of sentence length | 6.64 | 6.88 | 6.3 |
| share of very short sentences (3 words or fewer) | 0.221 | 0.198 | 0.219 |
| average paragraph length (words) | 39.3 | 30.3 | 26.2 |
| share of paragraphs that are dialogue lines (start with a dash) | 0.511 | 0.481 | 0.435 |
| share of first-person pronouns (я, меня, мой ...) | 0.0293 | 0.0284 | 0.0328 |
| share of second-person pronouns (ты, вы ...) | 0.0131 | 0.0127 | 0.0123 |
| vocabulary richness: different words in a 5000-word sample | 0.465 | 0.455 | 0.443 |
| exclamation marks per 1,000 words | 7.61 | 8.4 | 6.26 |
| question marks per 1,000 words | 12.9 | 11.4 | 10.7 |
| ellipses per 1,000 words | 7.83 | 5.54 | 4.98 |
| commas per 1,000 words | 117 | 113 | 112 |
| average chapter length (log of words) | 3.56 | 3.53 | 3.37 |
| average word length (letters) | 5.23 | 5.22 | 5.19 |
| share of long words (10 letters or more) | 0.0925 | 0.0913 | 0.087 |
| numbers per 1,000 words | 0.744 | 1.66 | 1.62 |
| Latin-alphabet words per 1,000 words | 0.492 | 0.721 | 0.953 |
