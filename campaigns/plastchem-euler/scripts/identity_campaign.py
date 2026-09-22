"""Owner decision A-3 D-IDENT. No stereo-layer requirement and no isotope mapping."""
from geometry_identity import verify as perceive_geometry

MODE='connectivity_first_block_with_computed_stereochemistry'

def decide(input_key, observations):
    observed={x['method']:x['full_inchikey'] for x in observations if x.get('full_inchikey')}
    matching={engine:key for engine,key in observed.items() if key.split('-')[0]==input_key.split('-')[0]}
    perceived=next(iter(matching.values()),next(iter(observed.values()),None))
    agreed=[engine for engine,key in observed.items() if key==perceived]
    return {'input_inchikey':input_key,'perceived_inchikey':perceived,
            'inchikey_after_optimization':perceived,'identity_policy':MODE,
            'identity_match_basis':'connectivity_first_block','identity_verified':bool(matching),
            'connectivity_match':bool(matching),'full_key_equal':perceived==input_key,
            'perception_engines_agreeing_on_perceived_key':agreed,
            'perception_engines_matching_connectivity':list(matching),
            'all_perception_engines_agree_on_full_key':len(observed)>=2 and len(set(observed.values()))==1,
            'perceived_keys_by_engine':observed,'identity_observations':observations}

def verify(xyz,input_key,smiles):
    observation=perceive_geometry(xyz,input_key,smiles)
    return decide(input_key,observation['identity_observations'])
