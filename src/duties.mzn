include "all_different.mzn";

int: n_people;
int: n_days;
set of int: PEOPLE = 1..n_people;
set of int: DAYS = 1..n_days;

array[PEOPLE] of bool: can_be_on_duty;
array[PEOPLE] of bool: usually_goes_to_sirius;
array[PEOPLE] of int: completed_duties;
array[PEOPLE] of bool: worked_previous_day;
array[DAYS] of bool: is_wednesday;
array[DAYS] of bool: is_thursday;
array[PEOPLE] of 1..2: platoon;
array[0..n_people] of int: platoon_with_zero = array1d(0..n_people, [0] ++ platoon);

bool: cleaning_enabled;
int: sirius_priority_multiplier;

int: preferred_gap_days;
int: spacing_weight;
int: balance_weight;
int: duty_role_weight;
int: cleaning_preferred_gap_days;
int: cleaning_spacing_weight;
int: cleaning_balance_weight;

array[DAYS] of var PEOPLE: duty;
array[DAYS] of var PEOPLE: orderly_1;
array[DAYS] of var PEOPLE: orderly_2;
array[PEOPLE, DAYS] of var bool: works;

% Ноль означает отсутствие назначения при отключённых уборках.
array[DAYS] of var 0..n_people: barracks_1_a;
array[DAYS] of var 0..n_people: barracks_1_b;
array[DAYS] of var 0..n_people: barracks_2_a;
array[DAYS] of var 0..n_people: barracks_2_b;
array[DAYS] of var 0..n_people: bathroom_a;
array[DAYS] of var 0..n_people: bathroom_b;
array[PEOPLE, DAYS] of var bool: cleans_barracks;
array[PEOPLE, DAYS] of var bool: cleans_bathrooms;
array[PEOPLE, DAYS] of var bool: cleans;

constraint forall(d in DAYS)(
    if cleaning_enabled then
        barracks_1_a[d] >= 1 /\ barracks_1_a[d] < barracks_1_b[d] /\
        barracks_2_a[d] >= 1 /\ barracks_2_a[d] < barracks_2_b[d] /\
        platoon_with_zero[barracks_1_a[d]] = 1 /\
        platoon_with_zero[barracks_1_b[d]] = 1 /\
        platoon_with_zero[barracks_2_a[d]] = 2 /\
        platoon_with_zero[barracks_2_b[d]] = 2 /\
        bathroom_a[d] >= 1 /\
        bathroom_a[d] < bathroom_b[d] /\
        all_different([barracks_1_a[d], barracks_1_b[d],
                       barracks_2_a[d], barracks_2_b[d], bathroom_a[d], bathroom_b[d]])
    else
        barracks_1_a[d] = 0 /\ barracks_1_b[d] = 0 /\
        barracks_2_a[d] = 0 /\ barracks_2_b[d] = 0 /\
        bathroom_a[d] = 0 /\ bathroom_b[d] = 0
    endif
);

constraint forall(p in PEOPLE, d in DAYS)(
    (cleans_barracks[p,d] <-> (barracks_1_a[d] = p \/ barracks_1_b[d] = p \/
                              barracks_2_a[d] = p \/ barracks_2_b[d] = p)) /\
    (cleans_bathrooms[p,d] <-> (bathroom_a[d] = p \/ bathroom_b[d] = p)) /\
    (cleans[p,d] <-> (cleans_barracks[p,d] \/ cleans_bathrooms[p,d])) /\
    not (cleans[p,d] /\ works[p,d])
);

constraint forall(p in PEOPLE, d in 2..n_days)(
    not (cleans[p,d] /\ works[p,d-1])
);
constraint forall(p in PEOPLE where worked_previous_day[p])(not cleans[p,1]);

% В среду трое из не ездящих в Сириус находятся в наряде и в четверг
% не убирают. Эта вытекающая из правил граница облегчает поиск оптимума.
constraint forall(d in DAYS where cleaning_enabled /\ is_thursday[d] /\ d > 1)(
    sum(p in PEOPLE where usually_goes_to_sirius[p])(bool2int(cleans[p,d]))
    >= max(0, 9 - sum(p in PEOPLE)(bool2int(not usually_goes_to_sirius[p])))
);

% Три разных человека в сутки. Дневальные взаимозаменяемы.
constraint forall(d in DAYS)(
    duty[d] != orderly_1[d] /\
    duty[d] != orderly_2[d] /\
    orderly_1[d] < orderly_2[d] /\
    can_be_on_duty[duty[d]]
);

constraint forall(p in PEOPLE, d in DAYS)(
    works[p, d] <->
        (duty[d] = p \/ orderly_1[d] = p \/ orderly_2[d] = p)
);

% Оба вида наряда запрещены в среду для ездящих в Сириус.
constraint forall(p in PEOPLE, d in DAYS
                  where usually_goes_to_sirius[p] /\ is_wednesday[d])(
    not works[p, d]
);

constraint forall(p in PEOPLE, d in 1..n_days-1)(
    not (works[p, d] /\ works[p, d+1])
);

constraint forall(p in PEOPLE where worked_previous_day[p])(
    not works[p, 1]
);

array[PEOPLE] of var 0..n_days: monthly_count =
    [sum(d in DAYS)(bool2int(works[p, d])) | p in PEOPLE];

% Сравниваем общую нагрузку, включая историю до расчётного месяца.
var int: balance_penalty = sum(p, q in PEOPLE where p < q)(
    abs(completed_duties[p] + monthly_count[p]
        - completed_duties[q] - monthly_count[q])
);

% Чем ближе два наряда, тем больше штраф. Расстояния >= целевого не штрафуются.
var int: spacing_penalty = sum(
    p in PEOPLE, a, b in DAYS
    where a < b /\ b-a < preferred_gap_days
)(
    (preferred_gap_days - (b-a)) * (preferred_gap_days - (b-a))
    * bool2int(works[p, a] /\ works[p, b])
);

% Число дежурств всегда равно числу дней. Чтобы допущенные к дежурству
% преимущественно выполняли эту роль, штрафуем их назначения дневальными.
var int: duty_role_penalty = sum(d in DAYS)(
    bool2int(can_be_on_duty[orderly_1[d]])
    + bool2int(can_be_on_duty[orderly_2[d]])
);

array[PEOPLE] of var 0..n_days: barracks_count =
    [sum(d in DAYS)(bool2int(cleans_barracks[p,d])) | p in PEOPLE];
array[PEOPLE] of var 0..n_days: bathrooms_count =
    [sum(d in DAYS)(bool2int(cleans_bathrooms[p,d])) | p in PEOPLE];

% Кубари сравниваются внутри своего взвода, санузлы — между всеми людьми.
var int: cleaning_balance_penalty =
    sum(p,q in PEOPLE where p < q /\ platoon[p] = platoon[q])(
        abs(barracks_count[p] - barracks_count[q])
    ) + sum(p,q in PEOPLE where p < q)(abs(bathrooms_count[p] - bathrooms_count[q]));

% Интервалы учитываются между любыми уборками, включая смену типа уборки.
var int: cleaning_spacing_penalty = sum(
    p in PEOPLE, a,b in DAYS where a < b /\ b-a < cleaning_preferred_gap_days
)(
    (cleaning_preferred_gap_days - (b-a)) * (cleaning_preferred_gap_days - (b-a))
    * bool2int(cleans[p,a] /\ cleans[p,b])
);

var int: sirius_cleaning_penalty = sum(
    p in PEOPLE, d in DAYS where usually_goes_to_sirius[p] /\ is_thursday[d]
)(bool2int(cleans[p,d]));

var int: penalty = sirius_priority_multiplier * sirius_cleaning_penalty
                   + balance_weight * balance_penalty
                   + spacing_weight * spacing_penalty
                   + duty_role_weight * duty_role_penalty
                   + cleaning_balance_weight * cleaning_balance_penalty
                   + cleaning_spacing_weight * cleaning_spacing_penalty;

solve minimize penalty;
