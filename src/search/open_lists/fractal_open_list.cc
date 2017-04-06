#include "fractal_open_list.h"
#include "../evaluators/depth_evaluator.h"

#include "open_list.h"

#include "../option_parser.h"
#include "../plugin.h"

#include "../utils/rng.h"
#include "../utils/memory.h"
#include "../utils/system.h"
#include "../utils/logging.h"

#include <cassert>
#include <deque>
#include <map>
#include <utility>
#include <vector>
#include <iostream>

using namespace std;

template<class Entry>
FractalOpenList<Entry>::FractalOpenList(const Options &opts)
    : TypedTiebreakingOpenList<Entry>(opts), max_depth(opts.get<int>("max_depth")){
    assert(max_depth>0);
}

template<class Entry>
int FractalOpenList<Entry>::random_index_with_size_diff(const vector<uint> &records, int dim)
{
    vector<int> indices;
    uint depth = 0;
    for (auto &record : records){
        depth++;
        if(depth*dim > record){
            indices.push_back(depth);
        }
    }
    if (records.empty())
    {
        return 0;
    }
    else if (indices.empty())
    {
        return -1;
    }
    else
    {
        return g_rng(indices.size());
    }
}

template<class Entry>
int FractalOpenList<Entry>::first_index_with_size_diff(const vector<uint> &records, int dim)
{
    uint depth = 0;
    for (auto &record : records){
        depth++;
        if(depth*dim > record){
            return depth;
        }
    }
    return records.empty() ? 0 : -1;
}

template<class Entry>
Entry FractalOpenList<Entry>::remove_min(vector<int> *key) {
    assert(this->size > 0);
    --(this->size);
    assert(!this->buckets.empty());
    auto it = this->buckets.begin();  // sorted buckets
    assert(it != this->buckets.end());
    assert(!it->second.empty());
    if (key) {
        assert(key->empty());
        *key = it->first;
    }
    auto &tbuckets = it->second;
    assert(!tbuckets.empty());
    auto &records = expansion_records[it->first];
    auto &dim = current_dimension[it->first];
    if (records.empty()){
        records.resize(32);
    }
retry:
    int bucket_i =
        this->stochastic ?
        random_index_with_size_diff(records,dim) :
        first_index_with_size_diff(records,dim);
    if(bucket_i < 0){
        dim++;
        cout << "Increased dimension " << dim << " @ key " << it->first << endl;
        goto retry;
    }
    if ((uint)(bucket_i) >= records.size()){
        records.resize(records.size()*2);
    }
    records[bucket_i]++;
    auto it2 = tbuckets.begin() + bucket_i;
    auto &tbucket = it2->second;
    assert(!tbucket.empty());
    
    Entry result = pop_bucket<Entry,Bucket<Entry>>(tbucket, this->queue_type);
    if (tbucket.empty()){
        tbuckets.erase(it2);
        if (tbuckets.empty()){
            this->buckets.erase(it);
        }
    }
    return result;
}

FractalOpenListFactory::FractalOpenListFactory(const Options &options)
    : options(options) {
}

unique_ptr<StateOpenList>
FractalOpenListFactory::create_state_open_list() {
    return Utils::make_unique_ptr<FractalOpenList<StateOpenListEntry>>(options);
}

unique_ptr<EdgeOpenList>
FractalOpenListFactory::create_edge_open_list() {
    return Utils::make_unique_ptr<FractalOpenList<EdgeOpenListEntry>>(options);
}

static shared_ptr<OpenListFactory> _parse(OptionParser &parser) {
    parser.document_synopsis("Typed Tie-breaking open list",
                             "Select a bucket with minimum <evals>,"
                             "then within the bucket, diversify the search among type buckets."
                             "Each type bucket labeled by the values of <type_evals>.");
    parser.add_list_option<ScalarEvaluator *>("evals",
                                              "Scalar evaluators."
                                              "Results are sorted according to the dictionary order,"
                                              "preferring smaller numbers.");
    parser.add_option<int>("max_depth", "Max depth in a plateau. UNUSED","1000000");
    add_queue_type_option_to_parser(parser);
    parser.add_option<bool>(
        "pref_only",
        "insert only nodes generated by preferred operators", "false");
    parser.add_option<bool>(
        "unsafe_pruning",
        "allow unsafe pruning when the main evaluator regards a state a dead end",
        "true");
    parser.add_option<bool>(
        "stochastic",
        "If true, type buckets are selected at random."
        "Otherwise, loop over the type buckets, i.e., "
        "the last type bucket is selected in the first iteration, then "
        "the second last type bucket is selected in the second iteration and so on."
        "After the first type bucket is selected, select the last type bucket again."
        , "true");
    parser.add_option<bool>("record", "record the depth", "false");
    Options opts = parser.parse();
    if (!opts.is_help_mode()){
        auto d = new DepthEvaluator::DepthEvaluator(opts);
        vector<ScalarEvaluator *> type_evals = {d};
        opts.set("type_evals",type_evals);
    }

    if (parser.dry_run())
        return nullptr;
    else
        return make_shared<FractalOpenListFactory>(opts);
}

static PluginShared<OpenListFactory> _plugin("fractal", _parse);
