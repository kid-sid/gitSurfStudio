import os
import sys
import json

# Add engine/src to sys.path so we can import the tools
engine_src = r"c:\Users\Sidhartha\Desktop\gitSurfStudio\engine"
sys.path.insert(0, engine_src)

from src.tools.symbol_extractor import SymbolExtractor
from src.tools.call_graph import CallGraph

def main():
    search_path = engine_src
    
    # 1. Extract Symbols
    print("Extracting symbols...")
    extractor = SymbolExtractor(cache_dir="/tmp/symbols_demo")
    symbol_index = extractor.extract_from_directory(search_path)
    
    # 2. Build Call Graph
    print("Building call graph...")
    cg = CallGraph(cache_dir="/tmp/call_graph_demo")
    cg.build_from_symbols(symbol_index, force_rebuild=True)
    
    # 3. Get Context for a known function
    print(f"Graph has {len(cg.node_info)} nodes.")
    target_func = "_is_overview_question"
    context = cg.get_context_for_function(target_func, depth=2)
    
    if "No call graph data found" in context:
        print(f"Warning: {target_func} not found. Available nodes sample: {list(cg.node_info.keys())[:10]}")

    output_path = r"c:\Users\Sidhartha\Desktop\gitSurfStudio\tmp\call_graph_demo.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(context)
    print(f"Output written to {output_path}")

if __name__ == "__main__":
    main()
