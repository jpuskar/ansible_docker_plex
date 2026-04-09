from configure import parse_option_settings


# The actual OptionSettings string from the Palworld DefaultPalWorldSettings.ini
# (2026-04-08 build). Used as the integration-level test case.
REAL_OPTION_SETTINGS = (
    '(Difficulty=None,RandomizerType=None,RandomizerSeed="",'
    "bIsRandomizerPalLevelRandom=False,DayTimeSpeedRate=1.000000,"
    "NightTimeSpeedRate=1.000000,ExpRate=1.000000,PalCaptureRate=1.000000,"
    "PalSpawnNumRate=1.000000,PalDamageRateAttack=1.000000,"
    "PalDamageRateDefense=1.000000,PlayerDamageRateAttack=1.000000,"
    "PlayerDamageRateDefense=1.000000,PlayerStomachDecreaceRate=1.000000,"
    "PlayerStaminaDecreaceRate=1.000000,PlayerAutoHPRegeneRate=1.000000,"
    "PlayerAutoHpRegeneRateInSleep=1.000000,PalStomachDecreaceRate=1.000000,"
    "PalStaminaDecreaceRate=1.000000,PalAutoHPRegeneRate=1.000000,"
    "PalAutoHpRegeneRateInSleep=1.000000,BuildObjectHpRate=1.000000,"
    "BuildObjectDamageRate=1.000000,"
    "BuildObjectDeteriorationDamageRate=1.000000,"
    "CollectionDropRate=1.000000,CollectionObjectHpRate=1.000000,"
    "CollectionObjectRespawnSpeedRate=1.000000,EnemyDropItemRate=1.000000,"
    "DeathPenalty=All,bEnablePlayerToPlayerDamage=False,"
    "bEnableFriendlyFire=False,bEnableInvaderEnemy=True,"
    "bActiveUNKO=False,bEnableAimAssistPad=True,"
    "bEnableAimAssistKeyboard=False,DropItemMaxNum=3000,"
    "DropItemMaxNum_UNKO=100,BaseCampMaxNum=128,"
    "BaseCampWorkerMaxNum=15,DropItemAliveMaxHours=1.000000,"
    "bAutoResetGuildNoOnlinePlayers=False,"
    "AutoResetGuildTimeNoOnlinePlayers=72.000000,"
    "GuildPlayerMaxNum=20,BaseCampMaxNumInGuild=4,"
    "PalEggDefaultHatchingTime=72.000000,WorkSpeedRate=1.000000,"
    "AutoSaveSpan=30.000000,bIsMultiplay=False,bIsPvP=False,"
    "bHardcore=False,bPalLost=False,"
    "bCharacterRecreateInHardcore=False,"
    "bCanPickupOtherGuildDeathPenaltyDrop=False,"
    "bEnableNonLoginPenalty=True,bEnableFastTravel=True,"
    "bEnableFastTravelOnlyBaseCamp=False,"
    "bIsStartLocationSelectByMap=True,"
    "bExistPlayerAfterLogout=False,"
    "bEnableDefenseOtherGuildPlayer=False,"
    "bInvisibleOtherGuildBaseCampAreaFX=False,"
    "bBuildAreaLimit=False,ItemWeightRate=1.000000,"
    "CoopPlayerMaxNum=4,ServerPlayerMaxNum=32,"
    'ServerName="Default Palworld Server",ServerDescription="",'
    'AdminPassword="",ServerPassword="",'
    "bAllowClientMod=True,PublicPort=8211,"
    'PublicIP="",RCONEnabled=False,RCONPort=25575,Region="",'
    "bUseAuth=True,"
    'BanListURL="https://b.palworldgame.com/api/banlist.txt",'
    "RESTAPIEnabled=False,RESTAPIPort=8212,"
    "bShowPlayerList=False,ChatPostLimitPerMinute=30,"
    "CrossplayPlatforms=(Steam,Xbox,PS5,Mac),"
    "bIsUseBackupSaveData=True,LogFormatType=Text,"
    "bIsShowJoinLeftMessage=True,SupplyDropSpan=180,"
    "EnablePredatorBossPal=True,MaxBuildingLimitNum=0,"
    "ServerReplicatePawnCullDistance=15000.000000,"
    "bAllowGlobalPalboxExport=True,"
    "bAllowGlobalPalboxImport=False,"
    "EquipmentDurabilityDamageRate=1.000000,"
    "ItemContainerForceMarkDirtyInterval=1.000000,"
    "ItemCorruptionMultiplier=1.000000,DenyTechnologyList=,"
    "GuildRejoinCooldownMinutes=0,BlockRespawnTime=5.000000,"
    "RespawnPenaltyDurationThreshold=0.000000,"
    "RespawnPenaltyTimeScale=2.000000,"
    "bDisplayPvPItemNumOnWorldMap_BaseCamp=False,"
    "bDisplayPvPItemNumOnWorldMap_Player=False,"
    'AdditionalDropItemWhenPlayerKillingInPvPMode="PlayerDropItem",'
    "AdditionalDropItemNumWhenPlayerKillingInPvPMode=1,"
    "bAdditionalDropItemWhenPlayerKillingInPvPMode=False,"
    "bAllowEnhanceStat_Health=True,"
    "bAllowEnhanceStat_Attack=True,"
    "bAllowEnhanceStat_Stamina=True,"
    "bAllowEnhanceStat_Weight=True,"
    "bAllowEnhanceStat_WorkSpeed=True)"
)


class TestBasicParsing:
    def test_simple_key_value(self):
        result = parse_option_settings("(Foo=1,Bar=2)")
        assert result == {"Foo": "1", "Bar": "2"}

    def test_without_outer_parens(self):
        result = parse_option_settings("Foo=1,Bar=2")
        assert result == {"Foo": "1", "Bar": "2"}

    def test_empty_string(self):
        result = parse_option_settings("")
        assert result == {}

    def test_empty_parens(self):
        result = parse_option_settings("()")
        assert result == {}

    def test_single_key(self):
        result = parse_option_settings("(OnlyKey=OnlyVal)")
        assert result == {"OnlyKey": "OnlyVal"}


class TestNestedParentheses:
    """The bug that crashed the server: CrossplayPlatforms=(Steam,Xbox,PS5,Mac)."""

    def test_nested_parens_preserved(self):
        result = parse_option_settings(
            "(A=1,CrossplayPlatforms=(Steam,Xbox,PS5,Mac),B=2)"
        )
        assert result["A"] == "1"
        assert result["B"] == "2"
        assert result["CrossplayPlatforms"] == "(Steam,Xbox,PS5,Mac)"

    def test_multiple_nested_parens(self):
        result = parse_option_settings(
            "(X=(a,b),Y=5,Z=(c,d,e))"
        )
        assert result == {"X": "(a,b)", "Y": "5", "Z": "(c,d,e)"}

    def test_deeply_nested_parens(self):
        result = parse_option_settings("(A=((x,y),(z,w)),B=1)")
        assert result["A"] == "((x,y),(z,w))"
        assert result["B"] == "1"


class TestEmptyValues:
    """DenyTechnologyList= has an empty value."""

    def test_empty_value(self):
        result = parse_option_settings("(A=1,EmptyKey=,B=2)")
        assert result["A"] == "1"
        assert result["EmptyKey"] == ""
        assert result["B"] == "2"

    def test_empty_value_at_end(self):
        result = parse_option_settings("(A=1,EmptyKey=)")
        assert result["A"] == "1"
        assert result["EmptyKey"] == ""

    def test_trailing_comma_produces_empty_segment(self):
        """DenyTechnologyList=, leaves an empty segment after split — should be skipped."""
        result = parse_option_settings("(Before=1,DenyTechnologyList=,After=2)")
        assert result["Before"] == "1"
        assert result["DenyTechnologyList"] == ""
        assert result["After"] == "2"


class TestQuotedValues:
    def test_quoted_string_value(self):
        result = parse_option_settings('(ServerName="My Server",Port=8211)')
        assert result["ServerName"] == '"My Server"'
        assert result["Port"] == "8211"

    def test_equals_in_quoted_value(self):
        result = parse_option_settings('(URL="https://example.com/api?a=1",B=2)')
        assert result["URL"] == '"https://example.com/api?a=1"'
        assert result["B"] == "2"


class TestRealConfig:
    """Parse the actual DefaultPalWorldSettings.ini OptionSettings from the 2026-04-08 build."""

    def test_parses_without_error(self):
        result = parse_option_settings(REAL_OPTION_SETTINGS)
        assert isinstance(result, dict)
        assert len(result) > 50

    def test_crossplay_platforms_intact(self):
        result = parse_option_settings(REAL_OPTION_SETTINGS)
        assert result["CrossplayPlatforms"] == "(Steam,Xbox,PS5,Mac)"

    def test_deny_technology_list_empty(self):
        result = parse_option_settings(REAL_OPTION_SETTINGS)
        assert result["DenyTechnologyList"] == ""

    def test_known_values(self):
        result = parse_option_settings(REAL_OPTION_SETTINGS)
        assert result["Difficulty"] == "None"
        assert result["ExpRate"] == "1.000000"
        assert result["RCONEnabled"] == "False"
        assert result["RCONPort"] == "25575"
        assert result["ServerName"] == '"Default Palworld Server"'
        assert result["BanListURL"] == '"https://b.palworldgame.com/api/banlist.txt"'
        assert result["bAllowEnhanceStat_WorkSpeed"] == "True"

    def test_keys_after_nested_parens(self):
        """Keys that come after CrossplayPlatforms must parse correctly."""
        result = parse_option_settings(REAL_OPTION_SETTINGS)
        assert result["bIsUseBackupSaveData"] == "True"
        assert result["LogFormatType"] == "Text"

    def test_keys_after_empty_value(self):
        """Keys that come after DenyTechnologyList= must parse correctly."""
        result = parse_option_settings(REAL_OPTION_SETTINGS)
        assert result["GuildRejoinCooldownMinutes"] == "0"
        assert result["BlockRespawnTime"] == "5.000000"
