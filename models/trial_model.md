```plantuml
@startuml trial_model
    class CombinedPatient {
        /included_trials: Trial [*]
        /excluded_trials: Trial [*]
        filtered: bool
        filter_trials()
    }
    class Trial {
        inclusion_criteria: Criterion [*]
        exlusion_criteria: Criterion [*]
        filter_applied: bool
        excluded: bool
        filter_trial(TestResult [*])
        determine_filters(TestSet)
        apply_filters(TestResult [*])
    }
    class Criterion <<dict>> {
        is_inclusion: bool
        description: text
    }
    class TestFilter {
        comparison: function
        threshold: float
        filter_string: string
        apply_filter(TestResult)
    }
    class LabTest {
        name: string
        aliases: string [1..*]
        possible_loincs: string [1..*]
        possible_units: string [1..*]
    }
    class test_set <<module>>{
        alias_regex: regex
        criteria_regex: regex
        test_from_alias(string)
        test_from_loinc(string)
    }
    class TestResult {
        date: dateTime
        value: float
    }

    CombinedPatient *-- "~* trials" Trial
    CombinedPatient *-- "~* results" TestResult
    TestResult --> "test" LabTest
    Trial *-- "~* filters" TestFilter 
    Trial *-- "~* criteria" Criterion 
    TestFilter --> "test" LabTest
    test_set *-- "~* tests" LabTest
@enduml