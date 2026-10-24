"""Owner decision A-3 D-IDENT. No stereo-layer requirement and no isotope mapping.
A-13 (owner, 2026-10-13): a first-block match is verified only when no hydrogen left its input atom (hydrogen_identity.py)."""
from geometry_identity import verify as perceive_geometry
from hydrogen_identity import METHOD as HYDROGEN, hydrogen_row

MODE='connectivity_first_block_with_computed_stereochemistry'
NO_MATCH='No perception engine produced the required first-block match'

def decide(input_key, observations):
    observed={x['method']:x['full_inchikey'] for x in observations if x.get('full_inchikey')}
    matching={engine:key for engine,key in observed.items() if key.split('-')[0]==input_key.split('-')[0]}
    perceived=next(iter(matching.values()),next(iter(observed.values()),None))
    agreed=[engine for engine,key in observed.items() if key==perceived]
    hydrogen=next((x.get('error') for x in observations if x['method']==HYDROGEN and x.get('error')),None)
    return {'input_inchikey':input_key,'perceived_inchikey':perceived,
            'inchikey_after_optimization':perceived,'identity_policy':MODE,
            'identity_match_basis':'connectivity_first_block','identity_verified':bool(matching) and not hydrogen,
            'identity_rejection':None if matching and not hydrogen else NO_MATCH if not matching else hydrogen,
            'connectivity_match':bool(matching),'full_key_equal':perceived==input_key,
            'perception_engines_agreeing_on_perceived_key':agreed,
            'perception_engines_matching_connectivity':list(matching),
            'all_perception_engines_agree_on_full_key':len(observed)>=2 and len(set(observed.values()))==1,
            'perceived_keys_by_engine':observed,'identity_observations':observations}

def verify(xyz,input_key,smiles):
    observation=perceive_geometry(xyz,input_key,smiles)
    return decide(input_key,[*observation['identity_observations'],hydrogen_row(xyz,smiles)])
