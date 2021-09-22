import json
import requests
import pandas as pd
import numpy as np

class B2A:

# == constructor ==
    def __init__(self, domain_url: str, api_token: str) -> None: 
        self.url = domain_url
        self.user = api_token

        self.dir = {}
        self.assignments = {}

# == Main Functions (stage_course, unstage_course, pathway) == 

    def stage_course(self, course_id: int) -> None:
        df = pd.DataFrame(self.all_courses())
        course = df[df['course_id'] == course_id].iloc[0]
        if len(course) == 0:
            raise Exception("Course not found. Use the all_courses() function to see a list of available courses.")
        elif course['access_removed'] == True:
            raise Exception("Access removed for course.")
        else:
            groups = self.all_grade_groups_for_course(course_id)
            group_ids = {}
            all_assignments = {}
            lowest_grades = {}
            for group in groups:
                group_info = {}
                scored_pts, possible_pts, maximum_pts, assignments = self.course_group_stats(course_id, group['group_id'])
                group_info['group_name'] = group['group_name']
                group_info['group_weight'] = group['group_weight']
                group_info['group_current_grade'] = self.calculate_percentage(scored_pts, possible_pts)
                group_info['maximum_points'] = float(maximum_pts)
                group_info['scored_points'] = float(scored_pts)
                group_info['possible_points'] = float(possible_pts)

                group_assignments = []
                for a in assignments:
                    assignment_info = {}
                    assignment_info['name'] = a['assignment_name']
                    assignment_info['points_possible'] = a['points_possible']
                    assignment_info['score'] = a['score']
                    assignment_info['grade'] = self.calculate_percentage(a['score'], a['points_possible'])
                    assignment_info['percent_of_total_grade'] = 0
                    assignment_info['due_at'] = a['due_at']
                    if a['points_possible'] != None:
                        assignment_info['percent_of_total_grade'] = (a['points_possible'] / maximum_pts) * group['group_weight']
                    all_assignments[a['assignment_id']] = assignment_info

                    assignment_info['assignment_id'] = a['assignment_id']
                    group_assignments.append(assignment_info)

                graded_a = [d['assignment_id'] for d in group_assignments if (d['score'] != None) & (d['percent_of_total_grade'] > 0)]
                future_a = [d['assignment_id'] for d in group_assignments if (d['score'] == None) & (d['percent_of_total_grade'] > 0)]
                ungraded_a = [d['assignment_id'] for d in group_assignments if (d['points_possible'] == 0) | (d['percent_of_total_grade'] == 0) | (d['percent_of_total_grade'] == None)]

                a_dict = {}
                a_dict['graded'] = graded_a
                a_dict['future'] = future_a
                a_dict['ungraded'] = ungraded_a
                group_info['assignments'] = a_dict

                group_ids[group['group_id']] = group_info

            self.assignments[course_id] = all_assignments

            course_info = {}
            course_info['course_current_grade'] = course['course_current_score']
            course_info['course_name'] = course['course_name']
            course_info['grade_groups'] = group_ids

            self.dir[course_id] = course_info

            print(f"Successfully staged course {self.dir[course_id]['course_name']} for grade tracking.")

    def unstage_course(self, course_id: int) -> None:
        if course_id in self.dir:
            course_name = self.dir[course_id]['course_name']
            self.dir.pop(course_id)
            self.assignments.pop(course_id)
            print(f"Unstaged course {course_name} from grade tracking.")
        else:
            raise Exception("Cannot unstage course that was never staged.")

    def pathway(self, course_id: int, ideal_score: float, test_groups={}):

        initial, set_groups, outliers = self.set_initial_group_grades(course_id, ideal_score)

        for id, grade in test_groups.items():
            if id in initial:
                initial.pop(id)
            elif id in set_groups:
                set_groups.pop(id)
            else:
                raise Exception("Id does not exist")

        low_groups = []
        high_groups = []

        for k,v in outliers.items():
            if v == (True, True):
                low_groups.append(k)
                high_groups.append(k)
            elif v == (True, False):
                low_groups.append(k)
            elif v == (False, True):
                high_groups.append(k)

        less = self.calculate_grade(course_id, initial, set_groups, test_groups) < ideal_score

        path = self.recursive(course_id, initial, set_groups, test_groups, low_groups, high_groups, ideal_score, less)

        self.pathway_db(course_id, path, ideal_score)
        return path

# == Dashboard == 

    def pathway_db(self, course_id: int, path: dict, ideal_score: float):
        # Current grade report
        print()
        print(f"== Dashboard for course \"{self.dir[course_id]['course_name']}\" ==")
        print()
        overall_grade = self.dir[course_id]['course_current_grade']

        total_num_groups = len(self.dir[course_id]['grade_groups'])
        num_groups = len(path)
        print(f"  > {num_groups} Grade groups considered ({total_num_groups} total)")

        for id, grades in self.dir[course_id]['grade_groups'].items():
            info = self.dir[course_id]['grade_groups'][id]
            current_grade, remaining_assignments, avg_score_needed = self.group_progress(course_id, id, path[id])
            print(f"    > {info['group_name']}: Current grade {info['group_current_grade']}% with weight of {info['group_weight']}%")
            print(f"      > Pathway grade calculated: {path[id]}")
            if avg_score_needed == None:
                print(f"      > No remaining assignments")
            else:
                print(f"      > Avg score of {avg_score_needed} needed on {len(remaining_assignments)} remaining assignments:")
            for a in remaining_assignments:
                print(f"        > {a['assignment_name']}, {a['points_possible']} pts possible. (goal: {format(a['points_possible']*avg_score_needed/100, '.3f')} pts)")
        print()
        print("== End of Dashboard ==")

# == Algorithm (recursive, borderline, calc_borderline) == 

    def recursive(self, course_id: int, groups: dict, set_groups: dict, test_groups: dict, low_outliers: list, high_outliers: list, ideal_score: float, less_than: bool, count=0, m=1, n=0.25) -> dict:
        """
        Groups: group grades being modified
        set_groups: group grades that are done being modified (can contain groups with finalized grades)
        test_groups: group grades from user input (should contain groups with finalized grades), will not be touched
        """

        print(f"== Starting the {count}th iteration of the recursive function == ")
        print(f"  > initial {groups}, set {set_groups}, test {test_groups}")

        calculated_grade = self.calculate_grade(course_id, groups, set_groups, test_groups)

        print(f'  > Calculated grade {calculated_grade}')
        if len(groups) == 0:
            print(f"    > No more group grades able to be modified (all groups are finalized).")
            return set_groups

        if (calculated_grade < ideal_score) & less_than: # coming from low end
            print("    > Calculated grade is less than ideal score and coming from bottom")
            if (len(high_outliers) > 1) & ((count % 2 == 0) | (len(low_outliers) == 0)): # Increase avgs for groups with high outliers by 'm'
                print(f'    > Increase avgs for groups with high outliers by {m}')
                for id in high_outliers.copy():
                    if self.grade_possible(course_id, id, groups.get(id), m) == True: 
                        print(f'      > New grade is possible for {id} (prev grade {groups[id]})')
                        groups[id] = groups.get(id) + m # new grade is possible
                        print(f'      > New grade for {id}: {groups[id]}')
                    else:
                        print(f'      > New grade is not possible for {id} (prev grade {groups[id]})')
                        print(f'      > Set grade for {id}')
                        set_groups.update({id:groups.get(id)}) # group grade is set, should not be changed
                        groups.pop(id)
                        high_outliers.remove(id)
                        
                return self.recursive(course_id, groups, set_groups, test_groups, low_outliers, high_outliers, ideal_score, less_than, count+1, m, n)

            elif (len(low_outliers) > 0) & ((count % 2 == 1) | (len(high_outliers) == 0)): # Increase avgs for groups with low outliers by 'n'
                print(f'    > Increase avgs for groups with low outliers by {n}')
                for id in low_outliers.copy():
                    if self.grade_possible(course_id, id, groups.get(id), n) == True: 
                        print(f'      > New grade is possible for {id} (prev grade {groups[id]})')
                        groups[id] = groups.get(id) + n # new grade is possible
                        print(f'      > New grade for {id}: {groups[id]}')
                    else:
                        print(f'     > New grade is not possible for {id} (prev grade {groups[id]})')
                        print(f'     > Set grade for {id}')
                        set_groups.update({id:groups.get(id)}) # group grade is set, should not be changed
                        groups.pop(id)
                        low_outliers.remove(id)

                return self.recursive(course_id, groups, set_groups, test_groups, low_outliers, high_outliers, ideal_score, less_than, count+1, m, n)

            else: # increment by n for each group in groups (those where current grades == performance)
                print(f"    > Incrementing by {n} for each group with remaining assignments")
                cannot_adj = True # keeps track to see if all groups are non-adjustable
                for id in groups:
                    print(f'      > Prev grade for group {id}: {groups[id]}')
                    if self.grade_possible(course_id, id, groups.get(id), n) == True:
                        groups[id] = groups.get(id) + n
                        print(f'      > New grade for group {id}: {groups[id]}')
                        cannot_adj = False
                    else:
                        print(f'      > Adjustment not possible')
                for id in set_groups:
                    if len(self.dir[course_id]['grade_groups'][id]['assignments']['future']) > 0:
                        print(f'      > Prev grade for set_groups {id}: {set_groups[id]}')
                        if self.grade_possible(course_id, id, groups.get(id), n) == True:
                            set_groups[id] = set_groups.get(id) + n
                            print(f'      > New grade for set_groups {id}: {set_groups[id]}')
                            cannot_adj = False
                        else:
                            print(f'      > Adjustment not possible for {id}')

                if cannot_adj: # no more adjustment can be made
                    return self.borderline(course_id, groups, set_groups, test_groups, ideal_score, calculated_grade)

                return self.recursive(course_id, groups, set_groups, test_groups, low_outliers, high_outliers, ideal_score, less_than, count+1, m, n)

        elif (calculated_grade > ideal_score) & less_than: # ideal score reached from low end (calculated is greater than ideal score)
            print('    > Calculated grade greater than ideal score from low end')
            return self.borderline(course_id, groups, set_groups, test_groups, ideal_score, calculated_grade)

        elif (calculated_grade > ideal_score) & (not less_than): # coming from high end
            print("    > Calculated grade is greater than ideal score and coming from top")
            if (len(low_outliers) > 0) & ((count % 2 == 0) | (len(high_outliers) == 0)): # Decrease avgs for groups with low outliers by 'm'
                print(f'    > Decrease avgs for groups with high outliers by {m}')
                for id in low_outliers.copy():
                    if self.grade_possible(course_id, id, groups.get(id), -m) == True: 
                        print(f'      > New grade is possible for {id} (prev grade {groups[id]})')
                        groups[id] = groups.get(id) - m # new grade is possible
                        print(f'      > New grade for {id}: {groups[id]}')
                    else:
                        print(f'     > New grade is not possible for {id} (prev grade {groups[id]})')
                        print(f'     > Set grade for {id}')
                        set_groups.update({id:groups.get(id)}) # group grade is set, should not be changed
                        groups.pop(id)
                        low_outliers.remove(id)

                return self.recursive(course_id, groups, set_groups, test_groups, low_outliers, high_outliers, ideal_score, not less_than, count+1, m, n)

            elif (len(high_outliers) > 0) & ((count % 2 == 1) | (len(low_outliers) == 0)): # Decrease avgs for groups with high outliers by 'n'
                print(f'    > Decrease avgs for groups with high outliers by {n}')
                for id in high_outliers.copy():
                    if self.grade_possible(course_id, id, groups.get(id), -n) == True: 
                        print(f'      > New grade is possible for {id} (prev grade {groups[id]})')
                        groups[id] = groups.get(id) - n # new grade is possible
                        print(f'      > New grade for {id}: {groups[id]}')
                    else:
                        print(f'     > New grade is not possible for {id} (prev grade {groups[id]})')
                        print(f'     > Set grade for {id}')
                        set_groups.update({id:groups.get(id)}) # group grade is set, should not be changed
                        groups.pop(id)
                        high_outliers.remove(id)

                return self.recursive(course_id, groups, set_groups, test_groups, low_outliers, high_outliers, ideal_score, not less_than, count+1, m, n)

            else: # decrement by n for each group in groups (those where current grades == performance)
                print(f'    > Decrementing by {n} for each group with remaining assignments')
                cannot_adj = True
                for id in groups:
                    print(f'      > Prev grade for group {id}: {groups[id]}')
                    if self.grade_possible(course_id, id, groups[id], -n) == True:
                        groups[id] = groups.get(id) - n
                        print(f'      > New grade for group {id}: {groups[id]}')
                        cannot_adj = False
                    else:
                        print(f'      > Adjustment not possible for {id}')
                for id in set_groups:
                    if len(self.dir[course_id]['grade_groups'][id]['assignments']['future']) > 0:
                        print(f'      > Prev grade for set_groups {id}: {set_groups[id]}')
                        if self.grade_possible(course_id, id, set_groups[id], -n) == True:
                            set_groups[id] = set_groups.get(id) - n
                            print(f'      > New grade for set_groups {id}: {set_groups[id]}')
                            cannot_adj = False
                        else:
                            print(f'      > Adjustment not possible for {id}')

                if cannot_adj: # no more adjustment can be made
                    return self.borderline(course_id, groups, set_groups, test_groups, ideal_score, calculated_grade)
                    
                return self.recursive(course_id, groups, set_groups, test_groups, low_outliers, high_outliers, ideal_score, less_than, count+1, m, n)
        
        elif (calculated_grade < ideal_score) & (not less_than): # ideal score reached from lhigh end (calculated is lower than ideal score)
            print('    > Calculated grade lower than ideal score from high end')
            return self.borderline(course_id, groups, set_groups, test_groups, ideal_score, calculated_grade)

        elif calculated_grade == ideal_score:
            print("      > Calculated grade equals ideal score")
            copy = groups.copy()
            copy.update(set_groups)
            copy.update(test_groups)

            return copy

        else:
            raise Exception("Error in algorithm logic")

    def borderline(self, course_id: int, groups: dict, set_groups: dict, test_groups: dict, ideal_score: float, calculated_grade: float) -> dict:
        all_groups = groups.copy()
        all_groups.update(set_groups)
        all_groups.update(test_groups)

        if calculated_grade < ideal_score:
            return self.calc_borderline(course_id, all_groups, test_groups, ideal_score, True)
        elif calculated_grade > ideal_score:
            return self.calc_borderline(course_id, all_groups, test_groups, ideal_score, False)
        else:
            return all_groups

    def calc_borderline(self, course_id: int, all_groups: dict, test_groups: dict, ideal_score: float, less_than: bool) -> float:
        score = ideal_score
        var = 0

        for id, grade in all_groups.items():
            group_weight = self.dir[course_id]['grade_groups'][id]['group_weight'] / 100
            if (len(self.dir[course_id]['grade_groups'][id]['assignments']['future']) > 0) & (id not in test_groups):
                score -= group_weight * grade
                if less_than:
                    var += group_weight
                else:
                    var -= group_weight
            else:
                score -= group_weight * grade
        
        x = float(format(score/var, ".4f"))
        copy_groups = all_groups.copy()

        for id, grade in copy_groups.items():
            if (len(self.dir[course_id]['grade_groups'][id]['assignments']['future']) > 0) & (id not in test_groups):
                copy_groups[id] = copy_groups.get(id) + x

        return copy_groups

# == Algorithm Calculations (outliers_for_course, set_initial_group_grades, calculate_outliers, grade_possible) == 

    def outliers_for_course(self, course_id: int) -> dict:
        """
        {group_id: ([not outliers], [low outliers], [high outliers])}
        automatically does not count groups where weight is zero and no more assignments
        returns none where there are no graded assignments
        """
        gg = self.dir[course_id]['grade_groups']
        group_grades = {}
        for id, info in gg.items():
            if (gg[id]['group_weight'] != 0) & (len(gg[id]['assignments']['future']) > 0):
                assignment_grades = []
                assignment_ids = info['assignments']['graded']
                for a in assignment_ids:
                    assignment_grades.append(self.assignments[course_id][a]['grade'])

                if len(assignment_grades) == 0:
                    group_grades[id] = None
                else:
                    outliers = self.calculate_outliers(assignment_grades)
                    group_grades[id] = outliers
        return group_grades

    def set_initial_group_grades(self, course_id: int, ideal_score: float) -> tuple:
        """
        ({group_id: avg % grade for no outliers scores}, {group_id: current % grade}, {group_id: (has low outliers, has high outliers)})
        if score was None, sets to ideal_score
        """
        outliers = self.outliers_for_course(course_id)
        initial = {}
        set_group = {}
        has_low_high = {} # outliers
        for id, scores in outliers.items():
            assignments = outliers.get(id)
            weight = self.dir[course_id]['grade_groups'][id]['group_weight']
            if assignments == None:
                initial[id] = ideal_score
                has_low_high[id] = (False, False)
            else:
                no, low, high = assignments
                avg_no_outliers = sum(no) / len(no)

                # checking if performance score is reasonable given # of future assignments
                # Reasonable if % needed for remaining assignments is <= the avg of no outliers
                if self.group_progress(course_id, id, avg_no_outliers)[2] <= avg_no_outliers:
                    initial[id] = avg_no_outliers
                else:
                    initial[id] = self.dir[course_id]['grade_groups'][id]['group_current_grade']
                has_low_high[id] = (len(low) > 0, len(high) > 0)

        for id, info in self.dir[course_id]['grade_groups'].items():
            if (len(info['assignments']['future']) == 0) & (info['group_weight'] > 0):
                set_group[id] = info['group_current_grade']

        return (initial, set_group, has_low_high)

    def calculate_outliers(self, lst: list, m=2) -> tuple:
        """
        ([not outliers], [low outliers], [high outliers])
        """
        array = np.array(lst)
        d = np.abs(array - np.median(array))
        mdev = np.median(d)
        s = d / (mdev if mdev else 1.)

        no_outliers = array[s < m].tolist()
        max_grade = max(no_outliers)
        min_grade = min(no_outliers)
        return (no_outliers, array[(array < min_grade) & (s >= m)].tolist(), array[(array > max_grade) & (s >= m)].tolist())

    def grade_possible(self, course_id: int, group_id: int, grade: float, adj: float, diff=5.) -> bool:
        """
        Grade is possible (returns true) if: 
        0 <= grade <= 100
        grade < remaining_percent + diff
        """
        max_pts = self.dir[course_id]['grade_groups'][group_id]['maximum_points']
        scored_pts = self.dir[course_id]['grade_groups'][group_id]['scored_points']
        possible_pts = self.dir[course_id]['grade_groups'][group_id]['possible_points']
        remaining_percent = self.calculate_remaining_percentage_for_ideal(grade, max_pts, scored_pts, possible_pts)

        adjusted_grade = grade + adj
        if (adjusted_grade < 0) | (adjusted_grade > 100):
            return False
        elif remaining_percent > 100:
            return False
        elif remaining_percent > (adjusted_grade + diff):
            return False
        else:
            return True

# == Useful Stuff (group_progress, course_group_stats) == 

    def group_progress(self, course_id: int, group_id: int, ideal_score: float):
        """
        (current grade, [assignments to go], avg score needed on remaining assignments)
        ideal score is % for group
        """
        group_info = self.dir[course_id]['grade_groups'][group_id]
        name = group_info['group_name']
        current_grade = group_info['group_current_grade']
        future = group_info['assignments']['future']
        points_maximum = group_info['maximum_points']
        points_scored = group_info['scored_points']
        points_possible = group_info['possible_points']

        if points_maximum == points_possible:
            return (current_grade, [], None)

        a_list = []
        
        for a_id in future:
            a_info = {}
            assignment = self.assignments[course_id][a_id]
            a_info['assignment_id'] = a_id
            a_info['assignment_name'] = assignment['name']
            a_info['points_possible'] = assignment['points_possible']
            a_info['percent_of_total_grade'] = assignment['percent_of_total_grade']
            a_list.append(a_info)

        remaining_percentage = self.calculate_remaining_percentage_for_ideal(ideal_score, points_maximum, points_scored, points_possible)
        return (current_grade, a_list, remaining_percentage)

    def course_group_stats(self, course_id: int, group_id: int) -> tuple:
        """
        (scored: int, possible: int, maximum: int, assignments: list) 
        current course group grade calculated from scored and possible
        """
        assignments = self.assignments_for_course_by_grade_group(course_id, group_id)
        df = pd.DataFrame(assignments)
        df = df.replace({np.nan: None})
        maximum = df['points_possible'].sum()
        df = df[df['score'].isna() == False]
        if len(df) == 0:
            return (0, 0, maximum, assignments)
        else:
            possible = df['points_possible'].sum()
            if possible == None:
                possible = 0
            scored = df['score'].sum()
            if scored == None:
                scored = 0
            return (scored, possible, maximum, assignments)

# == Standard Calculations (calculate_percentage, calculate_remaining_percentage_for_ideal, calculate_grade) == 

    def calculate_percentage(self, scored, possible) -> float:
        if (scored == None) or (scored == 0):
            return None
        percentage_str = format((scored / possible * 100.000), ".3f")
        return float(percentage_str)

    def calculate_remaining_percentage_for_ideal(self, ideal_score: float, max_pts: float, scored_pts: float, possible_pts: float) -> float:
        decimals = str(scored_pts)[::-1].find('.')
        points_needed = float(format((ideal_score / 100) * max_pts - scored_pts, ".3f"))
        return self.calculate_percentage(points_needed, max_pts - possible_pts) 

    def calculate_grade(self, course_id: int, groups: dict, set_groups={}, test_groups={}) -> float:
        """
        Returns calculated grade given grades for groups

        groups - {group_id: % grade}
        """
        sum = 0
        copy = groups.copy()
        copy.update(set_groups)
        copy.update(test_groups)

        for id, grade in copy.items():
            gg = self.dir[course_id]['grade_groups'].get(id, None)
            if gg == None:
                raise Exception(f"Unknown group id {id}")
            sum = sum + (grade / 100) * gg['group_weight']

        return float(format(sum, ".3f"))

# == Canvas (all_courses, all_grade_groups_for_course, assignments_for_course_by_grade_group, get_assignment_grade_for_course) ==

    def all_courses(self) -> list:
        L = [] # stores list of course dictionaries
        r = self.pagination(f"https://{self.url}/api/v1/users/self/courses?include[]=total_scores&per_page=50")
        for course in r:
            try:
                infos = {} # fill with name, weights, current_score
                course_id = course['id']
                course_name = course['name']
                course_weights = course['apply_assignment_group_weights']
                course_current_score = course['enrollments'][0]['computed_current_score']
                infos['course_id'] = course_id
                infos['course_name'] = course_name
                infos['course_weights'] = course_weights
                infos['course_current_score'] = course_current_score
                infos['access_removed'] = False
                L.append(infos)
            except KeyError:
                course_id = course['id']
                infos['course_id'] = course_id
                infos['course_name'] = np.nan
                infos['course_weights'] = np.nan
                infos['course_current_score'] = np.nan
                infos['access_removed'] = True
                L.append(infos)
        return L

    def all_grade_groups_for_course(self, course_id: int) -> list:
        r = self.get_request(f"https://{self.url}/api/v1/courses/{course_id}/assignment_groups?per_page=50")
        L = []
        for group in r:
            infos = {}
            group_id = group['id']
            group_name = group['name']
            group_weight = group['group_weight']
            infos['course_id'] = course_id
            infos['group_id'] = group_id
            infos['group_name'] = group_name
            infos['group_weight'] = group_weight
            L.append(infos)
        return L

    def assignments_for_course_by_grade_group(self, course_id: int, group_id: int) -> list:
        r = self.pagination(f"https://{self.url}/api/v1/courses/{course_id}/assignment_groups/{group_id}/assignments")
        L = []
        for a in r:
            info = {}
            assignment_id = a['id']
            assignment_group_id = a['assignment_group_id']
            due_date = a['due_at']
            assignment_name = a['name']
            points_possible = a['points_possible']

            grade_request = self.get_assignment_grade_for_course(course_id, assignment_id)

            info['course_id'] = course_id
            info['assignment_id'] = assignment_id
            info['assignment_name'] = assignment_name
            info['group_id'] = assignment_group_id
            info['points_possible'] = points_possible
            info['grade'] = grade_request['grade']
            info['score'] = grade_request['score']
            info['due_at'] = due_date

            L.append(info)
        return L 

    def get_assignment_grade_for_course(self, course_id: int, assignment_id: int) -> dict:
        r = self.get_request(f"https://{self.url}/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/self")
        d = {}
        # d["assignment_name"] = r['name']
        d['course_id'] = course_id
        d['assignment_id'] = r['assignment_id']
        d['grade'] = r['grade']
        d['score'] = r['score']
        d['state'] = r['workflow_state']
        return d

# == GET (get_request, pagination) ==

    def get_request(self, path: str) -> json:
        r = requests.get(path, headers={'Authorization': "Bearer " + self.user})
        if r.status_code != 200:
            raise Exception("Error in establishing GET request.")
        return r.json()

    def pagination(self, path: str) -> list:
        headers = {'Authorization': "Bearer " + self.user}
        r = requests.get(path, headers=headers)
        if r.status_code != 200:
            raise Exception("Error in establishing GET request.")
        data = []
        raw = r.json()
        for item in raw:
            data.append(item)

        while r.links['current']['url'] != r.links['last']['url']:
            r = requests.get(r.links['next']['url'], headers=headers)
            raw = r.json()
            for item in raw:
                data.append(item)
        return data






























