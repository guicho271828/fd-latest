/* -*- mode : c++ -*- */
#pragma once

#include "../scalar_evaluator.h"
#include <vector>
#include "../per_state_information.h"
#include "../global_state.h"

class Heuristic;
class Options;

namespace DepthEvaluator {
    using Key = std::vector<int>;
    class DepthEvaluator : public ScalarEvaluator {
        
        std::vector<ScalarEvaluator *> subevaluators;
        struct depthinfo {
            bool initialized = false;
            Key key;
            int depth;
        };
        PerStateInformation<depthinfo> db;
        int depth(EvaluationContext &eval_context);
        Key key(EvaluationContext &eval_context);

    public:
        explicit DepthEvaluator(const Options &opts);
        virtual ~DepthEvaluator() override = default;

        virtual EvaluationResult compute_result(
            EvaluationContext &eval_context) override;

        virtual void get_involved_heuristics(std::set<Heuristic *> &) override {}
    };
}

